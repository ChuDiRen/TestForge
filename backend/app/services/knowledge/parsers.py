"""文档解析器（LightRAG Native 解析器移植 + 第三方解析器注册表）。

原生支持：txt/md（多编码容错）、csv（→markdown 表）、pdf（pypdf 逐页文本）、
docx（python-docx：标题样式→markdown 标题、表格→markdown 表、内嵌图片→VLM 描述可选）。
MinerU/Docling 属可选外部引擎（与上游一致按需安装），未安装时给出明确指引而非静默降级。
第三方解析器经 register_parser 挂载（对齐 LightRAG ThirdPartyParser 机制）。
"""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger("shared.parsers")

TEXT_SUFFIXES = (".txt", ".md", ".markdown", ".rst")
PARSE_SUFFIXES = TEXT_SUFFIXES + (".csv", ".pdf", ".docx")

_THIRD_PARTY: dict[str, Callable[[str, bytes], "ParsedDoc"]] = {}


@dataclass
class ParsedDoc:
    title: str
    text: str
    meta: dict = field(default_factory=dict)  # images_total/images_described/tables 等解析画像


def register_parser(name: str, fn: Callable[[str, bytes], ParsedDoc]) -> None:
    """第三方解析器注册（对齐 LightRAG ThirdPartyParser）：fn(filename, bytes) → ParsedDoc。"""
    _THIRD_PARTY[name.lower()] = fn


def list_parsers() -> list[str]:
    return ["native"] + sorted(_THIRD_PARTY)


def parse_file(filename: str, data: bytes, parser: str = "native") -> ParsedDoc:
    """按扩展名分发解析。未知类型按纯文本容错。"""
    if parser != "native":
        fn = _THIRD_PARTY.get(parser.lower())
        if fn is None:
            raise ValueError(f"解析器 {parser} 未注册（已注册：{', '.join(list_parsers())}）")
        return fn(filename, data)
    low = filename.lower()
    if low.endswith(".pdf"):
        return _parse_pdf(filename, data)
    if low.endswith(".docx"):
        return _parse_docx(filename, data)
    if low.endswith(".csv"):
        return _parse_csv(filename, data)
    return _parse_text(filename, data)


def _decode(data: bytes) -> str:
    for enc in ("utf-8", "gb18030", "utf-16", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _title_from(filename: str, text: str) -> str:
    m = re.search(r"^\s{0,3}#\s+(.+)$", text, re.MULTILINE)
    if m:
        return m.group(1).strip()[:200]
    base = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return base.rsplit(".", 1)[0][:200]


def _parse_text(filename: str, data: bytes) -> ParsedDoc:
    text = _decode(data)
    return ParsedDoc(title=_title_from(filename, text), text=text, meta={"format": "text"})


def _parse_csv(filename: str, data: bytes) -> ParsedDoc:
    """CSV → markdown 表（抽取友好：LLM 读表比读裸逗号稳）。"""
    text = _decode(data)
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        return ParsedDoc(title=_title_from(filename, ""), text="", meta={"format": "csv", "rows": 0})
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    md = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    md += ["| " + " | ".join(c.replace("|", "\\|") for c in r) + " |" for r in rows[1:]]
    body = "\n".join(md)
    header = rows[0][0].strip() or _title_from(filename, "")
    return ParsedDoc(
        title=header[:200],
        text=f"## {header}\n\n{body}\n",
        meta={"format": "csv", "rows": len(rows) - 1, "columns": width},
    )


def _parse_pdf(filename: str, data: bytes) -> ParsedDoc:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("PDF 解析需要 pypdf：pip install pypdf") from exc
    reader = PdfReader(io.BytesIO(data))
    pages: list[str] = []
    for i, page in enumerate(reader.pages):
        try:
            t = (page.extract_text() or "").strip()
        except Exception:  # noqa: BLE001  加密页/字体异常跳过，不断整篇
            t = ""
        if t:
            pages.append(f"<!-- page {i + 1} -->\n{t}")
    text = "\n\n".join(pages)
    title = _title_from(filename, text)
    if reader.metadata and reader.metadata.title:
        title = str(reader.metadata.title)[:200]
    return ParsedDoc(title=title, text=text, meta={"format": "pdf", "pages": len(reader.pages)})


def _parse_docx(filename: str, data: bytes) -> ParsedDoc:
    """docx：Heading 样式→markdown 标题（对齐 smart_heading 的产出效果）、表格→markdown 表、
    内嵌图片→VLM 描述（vlm_model 未配置时跳过并计数，不阻塞解析）。"""
    try:
        import docx  # python-docx
    except ImportError as exc:
        raise RuntimeError("DOCX 解析需要 python-docx：pip install python-docx") from exc

    from app.core.tokenizer import truncate_tokens

    doc = docx.Document(io.BytesIO(data))
    out: list[str] = []
    tables = 0
    # body 顺序遍历（段落+表格混排时保持文档原始顺序）
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for block in doc.element.body.iterchildren():
        if block.tag.endswith("}p"):
            para = Paragraph(block, doc)
            style = (para.style.name or "").lower() if para.style is not None else ""
            text = para.text.strip()
            if not text:
                continue
            m = re.search(r"heading (\d)", style)
            if m:
                level = min(int(m.group(1)), 6)
                out.append(f"{'#' * level} {text}")
            else:
                out.append(text)
        elif block.tag.endswith("}tbl"):
            tables += 1
            out.append(_docx_table_md(Table(block, doc)))

    text = "\n\n".join(out)
    images_described, images_total = 0, 0
    vlm_caps = _vlm_describe_images(doc)
    for rel in doc.part.rels.values():
        if "image" in rel.reltype:
            images_total += 1
    if vlm_caps:
        caps_md = "\n\n".join(f"<!-- 图片 {i + 1}：{c} -->" for i, c in enumerate(vlm_caps))
        text += "\n\n## 文档内嵌图片内容\n\n" + caps_md
        images_described = len(vlm_caps)
    return ParsedDoc(
        title=_title_from(filename, text),
        text=truncate_tokens(text, 120_000),
        meta={"format": "docx", "tables": tables, "images_total": images_total, "images_described": images_described},
    )


def _docx_table_md(table: object) -> str:
    rows = []
    for r in getattr(table, "rows", []):  # type: ignore[attr-defined]
        cells = [c.text.strip().replace("|", "\\|").replace("\n", " ") for c in r.cells]
        rows.append("| " + " | ".join(cells) + " |")
    if not rows:
        return ""
    width = rows[0].count("|") - 1
    rows.insert(1, "| " + " | ".join(["---"] * max(width, 1)) + " |")
    return "\n".join(rows)


def _vlm_describe_images(doc: object) -> list[str]:
    """VLM 角色描述内嵌图片（LightRAG 多模态裁剪版）：未配置 VLM 时返回空（跳过不阻塞）。"""
    from app.core.config import get_settings

    s = get_settings()
    if not s.vlm_model or not (s.vlm_api_key or s.llm_api_key):
        return []
    try:
        import base64

        from langchain_core.messages import HumanMessage
        from langchain_openai import ChatOpenAI
        from pydantic import SecretStr

        base_url = s.vlm_base_url or s.llm_base_url
        api_key = s.vlm_api_key or s.llm_api_key
        vlm = ChatOpenAI(model=s.vlm_model, api_key=SecretStr(api_key), base_url=base_url, max_retries=1, timeout=30)
        caps: list[str] = []
        for shape in getattr(doc, "inline_shapes", []):  # type: ignore[attr-defined]
            try:
                rid = shape._inline.graphic.graphicData.pic.blipFill.blip.embed  # type: ignore[attr-defined]
                part = doc.part.related_parts[rid]  # type: ignore[attr-defined]
                b64 = base64.b64encode(part.blob).decode()
                mime = getattr(part, "content_type", "") or "image/png"
                resp = vlm.invoke([HumanMessage(content=[
                    {"type": "text", "text": "用一句中文概括这张图片的内容（图表/流程/截图）。"},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ])])
                caps.append(str(resp.content)[:300])
            except Exception as exc:  # noqa: BLE001
                log.debug("vlm image describe failed: %s", exc)
        return caps
    except ImportError as exc:
        log.debug("vlm unavailable: %s", exc)
        return []


# ---------------- 可选外部引擎（与上游一致：装了才能用，未装给明确指引） ----------------


def _require_mineru() -> Callable[[str, bytes], ParsedDoc]:
    try:
        import mineru  # type: ignore[import-not-found]  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "MinerU 未安装（pip install mineru）。MinerU 为 LightRAG 可选解析引擎，未安装时请用原生解析器（pdf/docx 已内置）。"
        ) from exc

    def _parse(filename: str, data: bytes) -> ParsedDoc:
        raise RuntimeError("MinerU 引擎接入需按其官方 API 包装；当前环境未完成适配。")

    return _parse


def _require_docling() -> Callable[[str, bytes], ParsedDoc]:
    try:
        import docling  # type: ignore[import-not-found]  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "Docling 未安装（pip install docling）。Docling 为 LightRAG 可选解析引擎，未安装时请用原生解析器。"
        ) from exc

    def _parse(filename: str, data: bytes) -> ParsedDoc:
        raise RuntimeError("Docling 引擎接入需按其官方 API 包装；当前环境未完成适配。")

    return _parse
