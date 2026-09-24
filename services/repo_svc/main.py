"""repo-svc 入口：gRPC RepoSvc（注册/拉取/函数列表）。"""

import logging

import grpc

from services.shared.base import pong
from services.shared.config import GRPC_PORTS
from services.shared.db import get_session
from services.shared.gen import testforge_pb2 as pb2
from services.shared.gen import testforge_pb2_grpc as pb2_grpc
from services.shared.grpc_server import run_server
from services.shared.logging import setup_logging
from services.shared.models import Functions

NAME = "repo-svc"
log = logging.getLogger(NAME)


class RepoServicer(pb2_grpc.RepoSvcServicer):
    def Ping(self, request, context):  # noqa: N802
        return pong(NAME)

    def Register(self, request, context):  # noqa: N802
        from services.repo_svc import service

        res = service.register(request.url, request.branch, request.credential_ref, request.webhook)
        return pb2.RepoInfo(id=res["id"], url=res["url"], branch=res["branch"], status=res["status"])

    def Pull(self, request, context):  # noqa: N802
        from services.repo_svc import service

        try:
            res = service.pull(request.repo_id)
        except KeyError as exc:
            context.abort(grpc.StatusCode.NOT_FOUND, str(exc))
        except Exception as exc:  # noqa: BLE001
            context.abort(grpc.StatusCode.INTERNAL, str(exc)[:300])
        info = pb2.RepoInfo(id=res["id"], url=res["url"], status=res["status"])
        return pb2.PullRes(
            repo=info,
            functions=res["functions"],
            call_edges=res["call_edges"],
            wiki_pages=res["wiki_pages"],
            steps=res["steps"],
        )

    def ListFunctions(self, request, context):  # noqa: N802
        with get_session() as sess:
            q = sess.query(Functions)
            if request.repo_id:
                q = q.filter(Functions.repo_id == request.repo_id)
            if request.module:
                q = q.filter(Functions.module == request.module)
            rows = q.order_by(Functions.id).all()
            data = [
                pb2.FunctionCard(
                    id=f.id, repo_id=f.repo_id, module=f.module, name=f.name,
                    signature=f.signature, source=f.source, file=f.file, line=f.line,
                )
                for f in rows
            ]
        yield from data


def register(server) -> None:
    pb2_grpc.add_RepoSvcServicer_to_server(RepoServicer(), server)


def main() -> None:
    setup_logging(NAME)
    log.info("starting %s", NAME)
    run_server(GRPC_PORTS[NAME], NAME, register)


if __name__ == "__main__":
    main()
