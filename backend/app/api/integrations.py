"""OpenWiki 完整移植·补充路由（2026-10-09）：

- /api/knowledge/import-url：URL 抓取入库（OpenWiki url_reader.rs 的 Web 版——
  微信公众号/GitHub 专用路由 + 通用 HTML 正文提取；无头 WebView/翻译属桌面能力不移植）
- /api/mcp/claude-desktop/*：MCP 一键接入 Claude Desktop（OpenWiki commands/mcp.rs 移植：
  备份后合并注入 mcpServers，绝不覆盖用户已有配置）

注意：/api/knowledge、/api/mcp 前缀下没有整型路径参数路由，尾部注册安全；
凡 /api/wiki/<静态段> 的新路由一律放 main.py 的 /api/wiki/{page_id} 之前。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.request
from pathlib import Path

from fastapi import APIRouter

from app.api.envelope import ApiError, ok
from app.schemas.knowledge import ImportUrlBody

router = APIRouter()
log = logging.getLogger("app.api.integrations")

# ---------------- URL 导入（OpenWiki url_reader.rs 移植·Web 版） ----------------

_MAX_CONTENT = 50_000
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TestForge/1.0"}
_SCRIPT_STYLE = re.compile(r"<(script|style|noscript|svg|nav|footer|header)\b[^>]*>.*?</\1>", re.S | re.I)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_BLOCK = re.compile(r"<(?:p|div|li|h[1-6]|pre|blockquote|td|section|article)\b[^>]*>(.*?)</(?:p|div|li|h[1-6]|pre|blockquote|td|section|article)>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_OG_TITLE = re.compile(r'property=["\']og:title["\'][^>]*content=["\'](.*?)["\']', re.I)


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=15) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read(_MAX_CONTENT * 4).decode(charset, errors="replace")


def _extract_weixin(html: str) -> str:
    """微信公众号正文：js_content 容器（url_reader.rs:218-341 的三级提取简化为容器级）。
    容器内嵌套 div 很多，非贪婪匹配会截断在第一个内层 </div>——直接抓到文末再剥标签。"""
    m = re.search(r'<div[^>]*id=["\']js_content["\'][^>]*>(.*)', html, re.S | re.I)
    if not m:
        return ""
    text = _TAG.sub("\n", m.group(1))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _extract_github(url: str) -> tuple[str, str]:
    """GitHub：仓库页取 README（REST API），blob 页转 raw 后按通用提取。"""
    m = re.match(r"https://github\.com/([^/]+)/([^/]+)(?:/(?:blob|tree)/[^/]+/.*)?$", url.rstrip("/") or "")
    if not m:
        return "", ""
    owner, repo = m.group(1), m.group(2).removesuffix(".git")
    api = f"https://api.github.com/repos/{owner}/{repo}/readme"
    req = urllib.request.Request(api, headers={**_UA, "Accept": "application/vnd.github.raw+json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return f"{owner}/{repo} README", resp.read(_MAX_CONTENT).decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        raise ApiError(502, f"GitHub README 获取失败：{exc}", 502) from exc


def _extract_generic(html: str) -> str:
    """通用正文：去 script/style → 块级文本按长度过滤 → 保序拼接。"""
    html = _COMMENT.sub("", html)
    html = _SCRIPT_STYLE.sub("", html)
    blocks = []
    for m in _BLOCK.finditer(html):
        text = re.sub(r"\s+", " ", _TAG.sub("", m.group(1))).strip()
        # 过滤导航/广告碎片：太短或纯符号
        if len(text) >= 20 and re.search(r"[\u4e00-\u9fff a-zA-Z]", text):
            blocks.append(text)
    # 兜底：块级提取失败直接整页剥标签
    if not blocks:
        blocks = [t.strip() for t in (_TAG.sub("\n", html)).split("\n") if len(t.strip()) >= 20]
    return "\n\n".join(blocks)[:_MAX_CONTENT]


@router.post("/api/knowledge/import-url")
def import_url(body: ImportUrlBody):
    """URL 一键入库知识文档（OpenWiki url_reader 移植）：抓取 → 正文提取 →
    敏感扫描 → rag_documents(kind=user_doc) 双索引。"""
    import hashlib

    from app.core.sensitive import scan_sensitive
    from app.services.knowledge.docstatus import mark
    from app.services.knowledge.rag import ensure_rag_documents_table, index_document

    url = body.url.strip()
    if not re.match(r"^https?://", url):
        raise ApiError(400, "url 必须是 http(s) 链接", 400)

    title = body.title.strip()
    try:
        if "github.com" in url:
            gh_title, content = _extract_github(url)
            title = title or gh_title
            html = ""
        else:
            html = _fetch(url)
            content = _extract_weixin(html) if "mp.weixin.qq.com" in url else _extract_generic(html)
        if not title:
            m = _OG_TITLE.search(html) or _TITLE.search(html)
            title = re.sub(r"\s+", " ", m.group(1)).strip()[:120] if m else url.split("//", 1)[1][:60]
    except ApiError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ApiError(502, f"抓取失败：{exc}", 502) from exc

    content = content.strip()
    if len(content) < 30:
        raise ApiError(422, "正文提取结果过短（<30 字）——该页面可能是纯 JS 渲染或反爬，手动粘贴正文入库", 422)

    hits = scan_sensitive(f"{title}\n{content}")
    if hits:
        raise ApiError(422, f"内容命中敏感信息（{'、'.join(hits)}），已拒绝入库", 422)

    ensure_rag_documents_table()
    doc_key = "userdoc:" + hashlib.sha1(f"{body.repo_id}:{url}".encode()).hexdigest()[:16]
    index_document(
        doc_key,
        "user_doc",
        title,
        content,
        repo_id=body.repo_id,
        meta={"source": "url-import", "source_url": url},
    )
    mark(body.repo_id, "user_doc", doc_key)
    return ok({"doc_key": doc_key, "title": title, "chars": len(content), "indexed": True})


# ---------------- MCP 一键接入 Claude Desktop（OpenWiki commands/mcp.rs 移植） ----------------


def _claude_config_path() -> Path:
    if os.name == "nt":
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / "Claude" / "claude_desktop_config.json"
    if os.uname().sysname == "Darwin":  # type: ignore[attr-defined]
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def _testforge_mcp_entry() -> dict:
    return {"command": "uv", "args": ["--project", str(Path(__file__).resolve().parents[2] / "backend"), "run", "python", "-m", "app.mcp_server"]}


@router.get("/api/mcp/claude-desktop/status")
def mcp_claude_status():
    """探测 Claude Desktop 配置：文件是否存在、是否已注入 TestForge。"""
    p = _claude_config_path()
    injected = False
    if p.exists():
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
            injected = "testforge" in (cfg.get("mcpServers") or {})
        except Exception:  # noqa: BLE001
            injected = False
    return ok({"config_path": str(p), "exists": p.exists(), "injected": injected})


@router.post("/api/mcp/claude-desktop/install")
def mcp_claude_install():
    """向 Claude Desktop 注入 mcpServers.testforge（先备份 .bak.<时间戳>，
    合并式写入——用户已有的其他 MCP server 一律不动，对齐 OpenWiki commands/mcp.rs:182-191）。"""
    p = _claude_config_path()
    cfg: dict = {}
    if p.exists():
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise ApiError(422, f"现有配置不是合法 JSON，请先手工修复 {p}：{exc}", 422) from exc
        backup = p.with_suffix(f".json.bak.{int(time.time())}")
        backup.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    servers = cfg.setdefault("mcpServers", {})
    servers["testforge"] = _testforge_mcp_entry()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("claude desktop mcp injected: %s", p)
    return ok({"config_path": str(p), "injected": True, "note": "重启 Claude Desktop 后生效"})
