"""相似用例 RAG：确定性 mock embedding + pgvector / python 余弦双路检索。

LLM_MODE=mock 不依赖外部 embedding API：token-hash 词袋向量（归一化），
保证同文本向量一致、语义近似（共享关键词）即可召回。
"""

from __future__ import annotations

import json
import logging
import math
import re

from services.shared.config import get_settings
from services.shared.db import get_session

log = logging.getLogger("shared.rag")

DIM = 256
_TOKEN = re.compile(r"[A-Za-z_]{2,}|[\u4e00-\u9fff]")


def embed(text: str) -> list[float]:
    """确定性词袋 hash 向量（归一化），mock 模式零外部依赖。"""
    vec = [0.0] * DIM
    for tok in _TOKEN.findall((text or "").lower()):
        h = 0
        for ch in tok:
            h = (h * 131 + ord(ch)) & 0xFFFFFFFF
        vec[h % DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 6) for v in vec]


def _pgvector_available(sess) -> bool:  # type: ignore[no-untyped-def]
    if get_settings().database_url.startswith("sqlite"):
        return False
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
        # sqlite / 表缺失：检索时走 python 余弦，无需存储


def similar_cases(content: str, limit: int = 5, layer: str = "") -> list[dict]:
    """检索 top-k 相似已入库用例。"""
    qvec = embed(content)
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
            # python 余弦兜底：全量已入库用例
            from services.shared.models import Cases

            q = sess.query(Cases).filter(Cases.status == "已入库")
            if layer:
                q = q.filter(Cases.layer == layer)
            cands = q.limit(500).all()
            scored = []
            for c in cands:
                cvec = embed(f"{c.title} {c.category} {c.target_function} {(c.schema_json or '')[:500]}")
                score = sum(a * b for a, b in zip(qvec, cvec))
                scored.append((c.code, -score))
            scored.sort(key=lambda x: x[1])
            codes = scored[: max(limit * 3, 15)]

    # 补全用例详情
    out: list[dict] = []
    if not codes:
        return out
    from services.shared.models import Cases

    wanted = [c for c, _ in codes]
    with get_session() as sess:
        rows = sess.query(Cases).filter(Cases.code.in_(wanted)).all()
        dist_map = {c: d for c, d in codes}
        for r in rows:
            out.append(
                {
                    "code": r.code,
                    "title": r.title,
                    "layer": r.layer,
                    "category": r.category,
                    "target_function": r.target_function,
                    "score": round(1.0 / (1.0 + dist_map.get(r.code, 1.0)), 4),
                }
            )
    out.sort(key=lambda x: -x["score"])
    return out[:limit]


def ensure_pgvector_table() -> None:
    """建向量表（幂等，pgvector 可用时）。"""
    if get_settings().database_url.startswith("sqlite"):
        return
    from sqlalchemy import text

    from services.shared.db import get_engine

    try:
        with get_engine().begin() as conn:
            conn.execute(text(f"CREATE TABLE IF NOT EXISTS cases_embedding (case_code TEXT PRIMARY KEY, embedding vector({DIM}))"))
        log.info("cases_embedding table ready (pgvector)")
    except Exception as exc:  # noqa: BLE001
        log.warning("pgvector table unavailable: %s", exc)
