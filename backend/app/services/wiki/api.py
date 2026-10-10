"""wiki-builder 平铺 API：分层编译/增量重建/模块页（原 gRPC WikiBuilder 契约）。"""

import logging

from app.core import health

log = logging.getLogger("wiki-builder.api")


def ping() -> dict:
    return health.ping("wiki-builder")


def rebuild(repo_id: int, from_rev: str = "", to_rev: str = "", changed_files: list[str] | None = None, trace_id: str = "") -> dict:
    """增量重建：changed_files=["__stale__"] 表示仅重编 stale 页；full 重建传 from_rev=""/changed_files=[]。"""
    from app.services.wiki import service

    try:
        return service.rebuild(
            repo_id=repo_id,
            from_rev=from_rev,
            to_rev=to_rev,
            changed_files=list(changed_files or []),
            trace_id=trace_id,
        )
    except Exception:
        log.exception("rebuild failed")
        raise


def get_module_page(repo_id: int, module: str = "") -> dict:
    from app.db.session import get_session
    from app.models import WikiPages

    with get_session() as sess:
        q = sess.query(WikiPages).filter(WikiPages.repo_id == repo_id, WikiPages.level == "module")
        if module:
            q = q.filter(WikiPages.module == module)
        page = q.first()
    if page is None:
        raise LookupError(f"模块页不存在: repo={repo_id} module={module}")
    return {
        "id": page.id,
        "repo_id": page.repo_id,
        "level": page.level,
        "title": page.title,
        "content_md": page.content_md,
        "rev": page.rev,
        "stale": page.stale,
        "deps": [],
    }
