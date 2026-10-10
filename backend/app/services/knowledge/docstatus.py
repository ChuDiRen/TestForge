"""知识摄入文档状态跟踪（LightRAG 文档状态机思路）。

每份文档（源文件/wiki 页/KG 抽取单元）在索引管线中留下 ok|failed|stale 状态，
`GET /api/knowledge/status` 聚合为摄入健康视图。
"""

from __future__ import annotations

import logging

from sqlalchemy import func

from app.db.session import get_session
from app.models import DocStatus

log = logging.getLogger("shared.docstatus")


def mark(repo_id: int, kind: str, doc_key: str, status: str = "ok", error: str = "", detail: str = "") -> None:
    """upsert 单文档状态（同 repo+kind+doc_key 覆盖）。"""
    with get_session() as sess:
        row = (
            sess.query(DocStatus)
            .filter(DocStatus.repo_id == repo_id, DocStatus.kind == kind, DocStatus.doc_key == doc_key)
            .first()
        )
        if row is None:
            sess.add(DocStatus(repo_id=repo_id, kind=kind, doc_key=doc_key[:500], status=status, error=error[:1000], detail=detail[:1000]))
        else:
            row.status = status
            row.error = error[:1000]
            if detail:
                row.detail = detail[:1000]
        sess.commit()


def summary(repo_id: int = 0) -> dict:
    """按 kind×status 聚合 + 最近失败清单。"""
    with get_session() as sess:
        q = sess.query(DocStatus.kind, DocStatus.status, func.count()).group_by(DocStatus.kind, DocStatus.status)
        if repo_id:
            q = q.filter(DocStatus.repo_id == repo_id)
        matrix: dict[str, dict[str, int]] = {}
        for kind, status, cnt in q.all():
            matrix.setdefault(kind, {})[status] = int(cnt)
        fq = sess.query(DocStatus).filter(DocStatus.status == "failed")
        if repo_id:
            fq = fq.filter(DocStatus.repo_id == repo_id)
        recent_failures = [
            {"repo_id": r.repo_id, "kind": r.kind, "doc_key": r.doc_key, "error": r.error, "updated_at": r.updated_at.isoformat()}
            for r in fq.order_by(DocStatus.updated_at.desc()).limit(10).all()
        ]
    return {"by_kind": matrix, "recent_failures": recent_failures}
