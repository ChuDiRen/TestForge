"""相似用例 RAG：本地确定性 embedding（token-hash 词袋，归一化）+ pgvector 检索。

不依赖外部 embedding API：同文本向量恒定、语义近似（共享关键词）即可召回。
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re

from services.shared.db import get_session

log = logging.getLogger("shared.rag")

DIM = 256
_TOKEN = re.compile(r"[A-Za-z_]{2,}|[\u4e00-\u9fff]")


_EMBED_CACHE: dict[str, list[float]] = {}
_EMBED_CACHE_MAX = 10_000


def _embed_cached(content: str) -> list[float]:
    key = hashlib.md5(content.encode("utf-8")).hexdigest()
    vec = _EMBED_CACHE.get(key)
    if vec is None:
        vec = embed(content)
        if len(_EMBED_CACHE) >= _EMBED_CACHE_MAX:
            _EMBED_CACHE.clear()  # 简单防膨胀：满则整体换血
        _EMBED_CACHE[key] = vec
    return vec


def embed(text: str) -> list[float]:
    """确定性词袋 hash 向量（归一化），零外部依赖。"""
    vec = [0.0] * DIM
    for tok in _TOKEN.findall((text or "").lower()):
        h = 0
        for ch in tok:
            h = (h * 131 + ord(ch)) & 0xFFFFFFFF
        vec[h % DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 6) for v in vec]


def _pgvector_available(sess) -> bool:  # type: ignore[no-untyped-def]
    try:
        from sqlalchemy import text

        sess.execute(text("SELECT 1 FROM cases_embedding LIMIT 1"))
        return True
    except Exception:  # noqa: BLE001
        sess.rollback()
        return False


def index_case(case_code: str, content: str) -> None:
    """用例入库时建立向量索引。"""
    vec = embed(content)
    with get_session() as sess:
        if _pgvector_available(sess):
            from sqlalchemy import text

            sess.execute(
                text("DELETE FROM cases_embedding WHERE case_code = :c"),
                {"c": case_code},
            )
            sess.execute(
                text("INSERT INTO cases_embedding (case_code, embedding) VALUES (:c, :e)"),
                {"c": case_code, "e": json.dumps(vec)},
            )
            sess.commit()
        # 表缺失：检索时走 python 余弦，无需存储


def similar_cases(content: str, limit: int = 5, layer: str = "") -> list[dict]:
    """检索 top-k 相似已入库用例（embedding 进程内缓存，避免每次重算）。"""
    from services.shared.models import Cases

    qvec = _embed_cached(content)
    codes: list[tuple[str, float]] = []
    with get_session() as sess:
        if _pgvector_available(sess):
            from sqlalchemy import text

            rows = sess.execute(
                text(
                    "SELECT case_code, embedding <-> CAST(:e AS vector) AS dist FROM cases_embedding ORDER BY dist LIMIT :k"
                ),
                {"e": json.dumps(qvec), "k": max(limit * 3, 15)},
            ).all()
            codes = [(str(r[0]), float(r[1])) for r in rows]
        if not codes:
            # python 余弦兜底：全量已入库用例（向量逐条缓存，二次查询零重算）
            q = sess.query(Cases.code, Cases.title, Cases.category, Cases.target_function, Cases.schema_json).filter(
                Cases.status == "已入库"
            )
            if layer:
                q = q.filter(Cases.layer == layer)
            scored: list[tuple[str, float]] = []
            for code, title, category, target_fn, schema_json in q.limit(500):
                cvec = _embed_cached(f"{title} {category} {target_fn} {(schema_json or '')[:500]}")
                score = sum(a * b for a, b in zip(qvec, cvec))
                scored.append((code, -score))
            scored.sort(key=lambda x: x[1])
            codes = scored[: max(limit * 3, 15)]

        if not codes:
            return []
        wanted = [c for c, _ in codes]
        rows = sess.query(Cases).filter(Cases.code.in_(wanted)).all()
    dist_map = dict(codes)
    out = [
        {
            "code": r.code,
            "title": r.title,
            "layer": r.layer,
            "category": r.category,
            "target_function": r.target_function,
            "score": round(1.0 / (1.0 + dist_map.get(r.code, 1.0)), 4),
        }
        for r in rows
    ]
    out.sort(key=lambda x: -x["score"])
    return out[:limit]


def ensure_pgvector_table() -> None:
    """建向量表（幂等）。"""
    from sqlalchemy import text

    from services.shared.db import get_engine

    try:
        with get_engine().begin() as conn:
            conn.execute(text(f"CREATE TABLE IF NOT EXISTS cases_embedding (case_code TEXT PRIMARY KEY, embedding vector({DIM}))"))
        log.info("cases_embedding table ready (pgvector)")
    except Exception as exc:  # noqa: BLE001
        log.warning("pgvector table unavailable: %s", exc)
