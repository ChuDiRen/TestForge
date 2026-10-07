"""Wiki 分析层（OpenWiki 特性移植，2026-10）：

- related_map: 页面 TF-IDF 余弦互链——对齐 OpenWiki ``link_pages_by_shared_tags``
  （IDF=ln((N+1)/(df+1))、阈值 0.3、每页 top-K 邻居）；中文按二元组切词，
  确定性计算，零 LLM 依赖。
- lint_repo: Wiki 健康体检——对齐 OpenWiki lint 的 finding 结构
  （stale / 超短页 / 重复标题 / 孤儿页 / 模块覆盖缺口），severity 三档。
"""

from __future__ import annotations

import math
import re
from collections import defaultdict

_TOP_TOKENS_PER_PAGE = 40
_RELATED_TOP_K = 6
# OpenWiki 用 0.3 是长文 tag 向量；TestForge 页面短、中文二元组稀释余弦，
# 0.05 + 标题三倍权在真实 wiki 上区分度更好（见 /api/wiki/{id}/related 实测）
_RELATED_THRESHOLD = 0.05
_TOKEN_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]{1,}")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _tokenize(text: str) -> list[str]:
    """ASCII 词 + 中文二元组（中文没有空格分词，bigram 是无依赖的折中）。"""
    tokens = [t.lower() for t in _TOKEN_RE.findall(text)]
    cjk = _CJK_RE.findall(text)
    tokens += [f"{a}{b}" for a, b in zip(cjk, cjk[1:])]
    return tokens


def related_map(pages: list[dict], top_k: int = _RELATED_TOP_K, threshold: float = _RELATED_THRESHOLD) -> dict[int, list[dict]]:
    """pages: [{id, title, text}] → {page_id: [{id, title, score}...]}（不含自身，降序）。"""
    n = len(pages)
    if n < 2:
        return {}
    docs: list[tuple[int, str, dict[str, float]]] = []
    df: dict[str, int] = defaultdict(int)
    for p in pages:
        tokens = _tokenize(f"{p.get('title', '')} {p.get('title', '')} {p.get('title', '')} {p.get('text', '')}")
        tf: dict[str, float] = defaultdict(float)
        for t in tokens:
            tf[t] += 1.0
        for t in tf:
            df[t] += 1
        docs.append((p["id"], p.get("title", ""), tf))

    # 加权 → 每页只留 top-N 个判别性 token（控住两两比较的规模）
    vectors: list[tuple[int, str, dict[str, float]]] = []
    for pid, title, tf in docs:
        weighted = {t: (1.0 + math.log(v)) * math.log((n + 1) / (df[t] + 1)) for t, v in tf.items()}
        keep = dict(sorted(weighted.items(), key=lambda kv: -kv[1])[:_TOP_TOKENS_PER_PAGE])
        norm = math.sqrt(sum(v * v for v in keep.values())) or 1.0
        vectors.append((pid, title, {t: v / norm for t, v in keep.items()}))

    # 倒排累加稀疏点积：token t 的倒排表内两两相乘即分解出的点积项，
    # 只比较共享 token 的页对；超高频 token（df>N/2）无判别力直接跳过
    inverted: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for pid, _, vec in vectors:
        for t, w in vec.items():
            inverted[t].append((pid, w))

    scores: dict[int, dict[int, float]] = defaultdict(dict)
    for t, postings in inverted.items():
        if len(postings) > n // 2:
            continue
        for idx_a, (a, wa) in enumerate(postings):
            for b, wb in postings[idx_a + 1:]:
                dot = wa * wb
                scores[a][b] = scores[a].get(b, 0.0) + dot
                scores[b][a] = scores[b].get(a, 0.0) + dot

    title_of = {pid: t for pid, t, _ in vectors}
    out: dict[int, list[dict]] = {}
    for pid, _, _ in vectors:
        neighbors = [
            {"id": other, "title": title_of[other], "score": round(s, 4)}
            for other, s in scores.get(pid, {}).items()
            if s >= threshold
        ]
        neighbors.sort(key=lambda x: -x["score"])
        out[pid] = neighbors[:top_k]
    return out


def lint_repo(pages: list[dict], indexed_modules: list[str], related: dict[int, list[dict]]) -> dict:
    """确定性体检。pages: [{id,title,level,module,stale,len}]; indexed_modules: 有函数索引的模块名。"""
    findings: list[dict] = []
    by_title: dict[str, list[int]] = defaultdict(list)
    for p in pages:
        by_title[p["title"].strip()].append(p["id"])
    for title, ids in by_title.items():
        if title and len(ids) > 1:
            findings.append({
                "type": "duplicate", "severity": "warning", "title": f"重复标题：{title}",
                "detail": f"{len(ids)} 个页面共用同一标题，检索时会互相稀释", "page_ids": ids,
            })

    stale = [p for p in pages if p.get("stale")]
    if stale:
        findings.append({
            "type": "stale", "severity": "warning", "title": f"{len(stale)} 个页面已过期（stale）",
            "detail": "源码变更后未重建，内容可能与当前代码不一致", "page_ids": [p["id"] for p in stale],
        })

    short = [p for p in pages if p["level"] != "repo" and p.get("len", 0) < 120]
    if short:
        findings.append({
            "type": "short_page", "severity": "info", "title": f"{len(short)} 个页面内容过短（<120 字）",
            "detail": "疑似生成失败或占位页，建议重建", "page_ids": [p["id"] for p in short[:50]],
        })

    wiki_modules = {p["module"] for p in pages if p.get("module")}
    gaps = [m for m in indexed_modules if m and m not in wiki_modules]
    if gaps:
        findings.append({
            "type": "coverage_gap", "severity": "warning", "title": f"{len(gaps)} 个模块没有 Wiki 页面",
            "detail": f"已索引但无模块页：{'、'.join(gaps[:8])}{'…' if len(gaps) > 8 else ''}",
            "modules": gaps,
        })

    orphans = [
        p["id"] for p in pages
        if p["level"] != "repo" and not related.get(p["id"])
    ]
    if orphans:
        findings.append({
            "type": "orphan", "severity": "info", "title": f"{len(orphans)} 个孤立页面（与其他页面无共同主题）",
            "detail": "互链为空说明内容与其他知识脱节，考虑补充内容或合并", "page_ids": orphans[:50],
        })

    severity_rank = {"critical": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: severity_rank.get(f["severity"], 3))
    return {
        "findings": findings,
        "checked": {"pages": len(pages), "modules": len(set(indexed_modules))},
    }
