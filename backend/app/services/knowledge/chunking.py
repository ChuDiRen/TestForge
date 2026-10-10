"""文档分块（LightRAG 四策略全量移植）：fixed / recursive / vector / paragraph。

- fixed（F）：定长 token 窗 + overlap；
- recursive（R）：递归字符分隔符切分（\\n\\n → \\n → 句号 → 空格），窗口内合并到 token 预算；
- vector（V）：句子 embedding 余弦距离突增处断开（语义漂移检测），窗口超预算强制断；
- paragraph（P）：段落语义——markdown 标题/空行段落为原生边界，小块合并进 token 预算，
  可选丢弃参考引用块（CHUNK_P_DROP_REFERENCES 对齐：参考文献/参考链接等不进抽取）。

所有策略返回统一 Chunk 列表：{index, content, tokens, meta}；chunk_size/overlap 走运行时设置。
"""

from __future__ import annotations

import logging
import re

from app.core.tokenizer import count_tokens

log = logging.getLogger("shared.chunking")

STRATEGIES = ("fixed", "recursive", "vector", "paragraph")

_REF_HEADING = re.compile(r"^\s{0,3}#{0,6}\s*(参考|引用|参考文献|参考链接|references|bibliography)\b", re.IGNORECASE)


def chunk_text(text: str, strategy: str = "paragraph", chunk_size: int = 1200, overlap: int = 100, drop_references: bool = True) -> list[dict]:
    """统一入口。strategy 不识别时回退 paragraph（原生语义边界最稳）。"""
    text = (text or "").strip()
    if not text:
        return []
    if strategy not in STRATEGIES:
        strategy = "paragraph"
    if strategy == "fixed":
        pieces = _split_fixed(text, chunk_size, overlap)
    elif strategy == "recursive":
        pieces = _split_recursive(text, chunk_size, overlap)
    elif strategy == "vector":
        pieces = _split_vector(text, chunk_size, overlap)
    else:
        pieces = _split_paragraph(text, chunk_size, overlap, drop_references)
    return [
        {"index": i, "content": p, "tokens": count_tokens(p), "meta": {"strategy": strategy}}
        for i, p in enumerate(pieces)
        if p.strip()
    ]


def _split_fixed(text: str, chunk_size: int, overlap: int) -> list[str]:
    """定长 token 窗：步长 = size - overlap。tail（上一窗尾部）按 token 精确截取并计入下一窗预算。"""
    if chunk_size <= overlap:
        overlap = max(chunk_size // 10, 0)
    step = max(chunk_size - overlap, 1)
    out: list[str] = []
    # 粗切句子避免 decode 出半字，再按 token 预算组窗
    sentences = re.split(r"(?<=[。！？.!?\n])", text)
    buf: list[str] = []
    buf_tokens = 0
    tail = ""  # 上一窗尾部 overlap 内容（计入下一窗预算）
    tail_tokens = 0
    for s in sentences:
        t = count_tokens(s)
        if t > chunk_size:  # 超长句硬截
            if buf:
                out.append(tail + "".join(buf))
                tail, tail_tokens = _tail("".join(buf), overlap), count_tokens(_tail("".join(buf), overlap))
                buf, buf_tokens = [], 0
            for k in range(0, len(s), step * 4):
                piece = s[k : k + step * 4]
                out.append(tail + piece)
                tail, tail_tokens = _tail(piece, overlap), count_tokens(_tail(piece, overlap))
                buf, buf_tokens = [], 0
            continue
        if buf_tokens + tail_tokens + t > chunk_size and buf:
            out.append(tail + "".join(buf))
            joined = "".join(buf)
            tail = _tail(joined, overlap)
            tail_tokens = count_tokens(tail)
            buf, buf_tokens = [], 0
        buf.append(s)
        buf_tokens += t
    if buf:
        out.append(tail + "".join(buf))
    return out


def _tail(text: str, max_tokens: int) -> str:
    from app.core.tokenizer import tail_tokens

    return tail_tokens(text, max_tokens)


def _split_recursive(text: str, chunk_size: int, overlap: int) -> list[str]:
    """递归字符分隔：从最强分隔符往下递归，直到每块不超 token 预算。"""
    separators = ["\n\n", "\n", "。", ".", "；", "; ", " ", ""]
    out: list[str] = []
    _recursive(text, chunk_size, separators, out)
    return _merge_pieces(out, chunk_size, overlap)


def _recursive(text: str, size: int, seps: list[str], out: list[str]) -> None:
    if count_tokens(text) <= size:
        out.append(text)
        return
    sep = ""
    rest: list[str] = seps
    for i, s in enumerate(seps):
        if s and s in text:
            sep = s
            rest = seps[i:]
            break
    if not sep:
        # 无分隔符可用：按 3*token 的字符窗硬截（拉丁字符/token≈4）
        out.extend(text[i : i + size * 3] for i in range(0, len(text), size * 3))
        return
    parts = text.split(sep)
    buf = ""
    for p in parts:
        cand = (buf + sep + p) if buf else p
        if count_tokens(cand) > size and buf:
            _recursive(buf, size, rest, out)
            buf = p
        else:
            buf = cand
    if buf.strip():
        _recursive(buf, size, rest, out)


def _split_vector(text: str, chunk_size: int, overlap: int) -> list[str]:
    """向量语义分块：句子级 embedding，相邻句余弦距离 > 均值+标准差处断开（主题漂移）。"""
    from app.services.knowledge.embedding import embed

    sentences = [s for s in re.split(r"(?<=[。！？.!?\n])", text) if s.strip()]
    if len(sentences) <= 2:
        return _split_fixed(text, chunk_size, overlap)
    vecs = [embed(s[:2000]) for s in sentences]

    def _cos(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b))

    dists = [1.0 - _cos(vecs[i], vecs[i + 1]) for i in range(len(vecs) - 1)]
    mean = sum(dists) / len(dists)
    std = (sum((d - mean) ** 2 for d in dists) / len(dists)) ** 0.5
    threshold = mean + std

    pieces: list[str] = []
    buf = ""
    buf_tokens = 0
    for i, s in enumerate(sentences):
        t = count_tokens(s)
        if buf_tokens + t > chunk_size and buf:
            pieces.append(buf)
            buf, buf_tokens = "", 0
        buf += s
        buf_tokens += t
        if i < len(dists) and dists[i] > threshold and buf_tokens >= chunk_size // 4:
            pieces.append(buf)
            buf, buf_tokens = "", 0
    if buf.strip():
        pieces.append(buf)
    return pieces


def _split_paragraph(text: str, chunk_size: int, overlap: int, drop_references: bool) -> list[str]:
    """段落语义：markdown 标题强制断开（原生文档边界），标题下内容按段落聚合到 token 预算。"""
    if drop_references:
        text = _drop_reference_blocks(text)
    lines = text.split("\n")
    blocks: list[str] = []
    buf = ""
    heading = False
    for line in lines:
        is_heading = bool(re.match(r"^\s{0,3}#{1,6}\s+\S", line))
        if is_heading and buf.strip():
            blocks.append(buf.rstrip())
            buf = ""
        elif re.match(r"^\s*$", line) and not heading and buf.strip() and count_tokens(buf) >= chunk_size:
            blocks.append(buf.rstrip())
            buf = ""
        buf += line + "\n"
        heading = is_heading
    if buf.strip():
        blocks.append(buf.rstrip())
    return _merge_pieces(blocks, chunk_size, overlap)


def _drop_reference_blocks(text: str) -> str:
    """丢弃参考引用块：标题命中参考/引用/references 后到下一个标题前的内容整段移除。"""
    lines = text.split("\n")
    out: list[str] = []
    skipping = False
    for line in lines:
        is_heading = bool(re.match(r"^\s{0,3}#{1,6}\s+\S", line))
        if is_heading:
            skipping = bool(_REF_HEADING.match(line))
        if not skipping:
            out.append(line)
    return "\n".join(out)


def _merge_pieces(pieces: list[str], chunk_size: int, overlap: int) -> list[str]:
    """小块聚合：相邻块合并到 token 预算内（保持原生边界不被打断），overlap 尾计入预算。"""
    # 先把天然超预算的单块（巨型段落）硬拆
    normalized: list[str] = []
    for p in pieces:
        p = p.strip()
        if not p:
            continue
        if count_tokens(p) > chunk_size:
            normalized.extend(_split_fixed(p, chunk_size, overlap))
        else:
            normalized.append(p)
    out: list[str] = []
    buf = ""
    buf_tokens = 0
    for p in normalized:
        t = count_tokens(p)
        if buf_tokens + t > chunk_size and buf:
            out.append(buf)
            tail = buf[-overlap * 3 :] if overlap else ""
            if tail and not tail.endswith(("\n", "。", ".", "！", "？")):
                cut = max(tail.rfind("\n"), tail.rfind("。"), tail.rfind(". "))
                tail = tail[cut + 1 :] if cut > 0 else ""
            cand = (tail + "\n" + p).strip() if tail else p
            # 尾巴可能把新窗顶爆预算：装得下才带尾巴
            buf, buf_tokens = (cand, count_tokens(cand)) if count_tokens(cand) <= chunk_size else (p, t)
        else:
            buf = (buf + "\n" + p).strip() if buf else p
            buf_tokens += t
    if buf.strip():
        out.append(buf)
    return out
