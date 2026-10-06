"""RAG 检索质量评估（LightRAG RAGAS 思路的确定性裁剪版）。

黄金集自动构建：以已入库用例为正例——查询串 = 目标函数+标题关键词，期望命中 = 该用例
+ 同目标函数的其他用例。无 LLM、无人工标注，随用例库增长自动扩容。

指标：recall@k 与 MRR，向量单路 vs 混合检索（向量+全文 RRF）对照——
用数字证明混合检索改造的收益，也为后续 rerank/调参提供回归基线。
"""

from __future__ import annotations

import json
import logging
import re

from services.shared.db import get_session
from services.shared.models import Cases

log = logging.getLogger("shared.rag_eval")

_TOKEN = re.compile(r"[A-Za-z_]{2,}|[\u4e00-\u9fff]{2,}")


def golden_set(limit: int = 50) -> list[dict]:
    """从已入库用例构建黄金集：查询串 → 期望命中的用例全码集合。"""
    with get_session() as sess:
        rows = (
            sess.query(Cases.code, Cases.title, Cases.target_function, Cases.category, Cases.module)
            .filter(Cases.status == "已入库")
            .order_by(Cases.id.desc())
            .limit(limit * 3)
            .all()
        )
    by_target: dict[str, list[tuple[str, str]]] = {}
    for code, title, tf, cat, module in rows:
        by_target.setdefault(tf or code, []).append((code, title))
    queries: list[dict] = []
    for tf, items in by_target.items():
        code, title = items[0]
        kws = " ".join(_TOKEN.findall(title.lower()))[:60]
        queries.append(
            {
                "query": f"{tf} {kws}".strip(),
                "relevant": sorted({c for c, _ in items}),
                "seed": code,
            }
        )
        if len(queries) >= limit:
            break
    return queries


def _metrics(ranked: list[str], relevant: set[str], k: int) -> tuple[float, float]:
    """返回 (recall@k, MRR)。"""
    topk = ranked[:k]
    hits = sum(1 for r in topk if r in relevant)
    recall = hits / len(relevant) if relevant else 0.0
    mrr = 0.0
    for i, r in enumerate(ranked):
        if r in relevant:
            mrr = 1.0 / (i + 1)
            break
    return recall, mrr


def evaluate(k: int = 5, limit: int = 50) -> dict:
    """黄金集评测：混合检索 vs 向量单路 对照。"""
    from services.shared.rag import _embed_cached, hybrid_search, tokenize

    queries = golden_set(limit=limit)
    if not queries:
        return {"queries": 0, "message": "无已入库用例，先跑生成/种子建库"}

    hybrid_scores: list[tuple[float, float]] = []
    vector_scores: list[tuple[float, float]] = []
    for q in queries:
        # 混合检索
        hits = hybrid_search(q["query"], kinds=("case",), limit=k)
        ranked_h = [h["doc_key"].removeprefix("case:") for h in hits]
        hybrid_scores.append(_metrics(ranked_h, set(q["relevant"]), k))
        # 向量单路（同 embedding，无 RRF/全文通道）
        with get_session() as sess:
            from sqlalchemy import text

            try:
                qvec = _embed_cached(tokenize(q["query"]) or q["query"])
                rows = sess.execute(
                    text(
                        "SELECT doc_key FROM rag_documents WHERE kind = 'case' "
                        "ORDER BY embedding <-> CAST(:e AS vector) LIMIT :k"
                    ),
                    {"e": json.dumps(qvec), "k": k},
                ).all()
                ranked_v = [str(r[0]).removeprefix("case:") for r in rows]
            except Exception:  # noqa: BLE001
                sess.rollback()
                ranked_v = ranked_h
        vector_scores.append(_metrics(ranked_v, set(q["relevant"]), k))

    def _agg(scores: list[tuple[float, float]]) -> dict:
        n = len(scores) or 1
        return {
            "recall_at_k": round(sum(s[0] for s in scores) / n, 4),
            "mrr": round(sum(s[1] for s in scores) / n, 4),
        }

    return {
        "queries": len(queries),
        "k": k,
        "hybrid": _agg(hybrid_scores),
        "vector_only": _agg(vector_scores),
        "detail": [
            {"query": q["query"], "relevant": q["relevant"], **dict(zip(("hybrid", "vector"), (h, v)))}
            for q, (h, v) in zip(queries, zip(hybrid_scores, vector_scores))
        ][:10],
    }
