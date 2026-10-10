"""Web 搜索兜底（LightRAG websearch 对齐）：检索枯竭时用 ddgs（DuckDuckGo）补上下文。

ddgs 未安装/离线时返回空并给出提示，不阻塞查询主链路。
"""

from __future__ import annotations

import logging

log = logging.getLogger("shared.websearch")


def available() -> bool:
    try:
        import ddgs  # noqa: F401

        return True
    except ImportError:
        return False


def search(query: str, max_results: int = 5) -> list[dict]:
    """返回 [{title, snippet, url}]；异常一律吞掉返回空（兜底不能变成故障点）。"""
    if not query.strip():
        return []
    try:
        from ddgs import DDGS

        out: list[dict] = []
        with DDGS() as ddgs_client:
            for r in ddgs_client.text(query, max_results=max_results):
                out.append(
                    {
                        "title": str(r.get("title") or "")[:200],
                        "snippet": str(r.get("body") or r.get("snippet") or "")[:600],
                        "url": str(r.get("href") or r.get("url") or "")[:500],
                    }
                )
        return out
    except ImportError:
        log.info("websearch 跳过：ddgs 未安装（pip install ddgs）")
        return []
    except Exception as exc:  # noqa: BLE001
        log.warning("websearch failed: %s", exc)
        return []
