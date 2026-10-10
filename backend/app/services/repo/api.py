"""repo-svc 平铺 API：注册/上传接入/拉取/函数清单（dict 进 dict 出，原 gRPC RepoSvc 契约）。"""

import logging

from app.core import health
from app.db.session import get_session
from app.models import Functions

log = logging.getLogger("repo-svc.api")


def ping() -> dict:
    return health.ping("repo-svc")


def register(url: str, branch: str = "main", credential_ref: str = "", webhook: bool = False) -> dict:
    from app.services.repo import service

    res = service.register(url, branch, credential_ref, webhook)
    return {"id": res["id"], "url": res["url"], "branch": res["branch"], "status": res["status"], "last_pull": ""}


def register_upload(name: str, path: str, branch: str = "main") -> dict:
    from app.services.repo import service

    res = service.register_upload(name, path, branch)
    return {
        "repo": {"id": res["id"], "url": res["url"], "branch": res["branch"], "status": res["status"], "last_pull": ""},
        "steps": res["steps"],
        "functions": res.get("functions", 0),
        "call_edges": res.get("call_edges", 0),
        "wiki_pages": res.get("wiki_pages", 0),
    }


def pull(repo_id: int) -> dict:
    from app.services.repo import service

    res = service.pull(repo_id)
    return {
        "repo": {"id": res["id"], "url": res["url"], "branch": "", "status": res["status"], "last_pull": ""},
        "functions": res["functions"],
        "call_edges": res["call_edges"],
        "wiki_pages": res["wiki_pages"],
        "steps": res["steps"],
        "changed_functions": res.get("changed_functions", []),
    }


def list_functions(repo_id: int = 0, module: str = "") -> list[dict]:
    with get_session() as sess:
        q = sess.query(Functions)
        if repo_id:
            q = q.filter(Functions.repo_id == repo_id)
        if module:
            q = q.filter(Functions.module == module)
        rows = q.order_by(Functions.id).all()
        return [
            {
                "id": f.id,
                "repo_id": f.repo_id,
                "module": f.module,
                "name": f.name,
                "signature": f.signature,
                "source": f.source,
                "file": f.file,
                "line": f.line,
                "language": f.language or "python",
            }
            for f in rows
        ]
