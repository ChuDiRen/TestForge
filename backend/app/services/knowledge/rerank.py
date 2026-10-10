"""Rerank 重排（LightRAG 2025.08+ 对齐）：jina / cohere / custom（OpenAI 风格 /rerank 协议）。

未配置 rerank_backend 时恒等返回（默认关闭，检索链路零外部依赖）。
查询链路在 RRF 融合后调用，对 top 候选按 query↔doc 相关性重排。
"""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger("shared.rerank")


def enabled() -> bool:
    from app.core.config import get_settings

    return bool(get_settings().rerank_backend)


def rerank(query: str, docs: list[str], top_n: int | None = None) -> list[tuple[int, float]]:
    """返回 [(原索引, 相关性分)] 按 分数降序。未启用/调用失败回退原序（score=下标负值保序）。"""
    from app.core.config import get_settings

    s = get_settings()
    backend = (s.rerank_backend or "").lower()
    if not backend or not docs:
        return [(i, -float(i)) for i in range(len(docs))]
    if not s.rerank_api_key or not s.rerank_model:
        log.warning("rerank_backend=%s 但 RERANK_API_KEY/RERANK_MODEL 未配置，跳过重排", backend)
        return [(i, -float(i)) for i in range(len(docs))]
    try:
        return _rerank_remote(query, docs, top_n or len(docs))
    except Exception as exc:  # noqa: BLE001  重排是增益不是依赖：失败回退原序
        log.warning("rerank failed, fallback to original order: %s", exc)
        return [(i, -float(i)) for i in range(len(docs))]


def _rerank_remote(query: str, docs: list[str], top_n: int) -> list[tuple[int, float]]:
    from app.core.config import get_settings

    s = get_settings()
    backend = s.rerank_backend.lower()
    headers = {"Authorization": f"Bearer {s.rerank_api_key}", "Content-Type": "application/json"}
    with httpx.Client(timeout=20) as client:
        if backend == "cohere":
            url = (s.rerank_base_url or "https://api.cohere.com").rstrip("/") + "/v2/rerank"
            body = {"model": s.rerank_model, "query": query, "documents": docs, "top_n": top_n}
            results = client.post(url, headers=headers, json=body).json()["results"]
            return [(int(r["index"]), float(r["relevance_score"])) for r in results]
        # jina / custom：{base_url}/rerank，返回 {results: [{index, relevance_score}]}
        url = (s.rerank_base_url or "https://api.jina.ai").rstrip("/") + "/rerank"
        body = {"model": s.rerank_model, "query": query, "documents": docs, "top_n": top_n}
        results = client.post(url, headers=headers, json=body).json()["results"]
        return [(int(r["index"]), float(r["relevance_score"])) for r in results]
