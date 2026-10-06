"""wiki-builder 入口：gRPC WikiBuilder（分层编译/增量重建/stale）。"""

import logging

import grpc

from services.shared.base import pong
from services.shared.config import GRPC_PORTS
from services.shared.db import get_session
from services.shared.gen import testforge_pb2 as pb2
from services.shared.gen import testforge_pb2_grpc as pb2_grpc
from services.shared.grpc_server import run_server
from services.shared.logutil import setup_logging
from services.shared.models import WikiPages

NAME = "wiki-builder"
log = logging.getLogger(NAME)


class WikiBuilderServicer(pb2_grpc.WikiBuilderServicer):
    def Ping(self, request, context):  # noqa: N802
        return pong(NAME)

    def Rebuild(self, request, context):  # noqa: N802
        from services.wiki_builder import service

        try:
            res = service.rebuild(
                repo_id=request.repo_id,
                from_rev=request.from_rev,
                to_rev=request.to_rev,
                changed_files=list(request.changed_files),
                trace_id=request.trace_id,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("rebuild failed")
            context.abort(grpc.StatusCode.INTERNAL, str(exc)[:300])
        return pb2.RebuildResult(
            pages_rebuilt=res["pages_rebuilt"],
            pages_stale=res["pages_stale"],
            rev_bumped=res["rev_bumped"],
        )

    def GetModulePage(self, request, context):  # noqa: N802
        with get_session() as sess:
            q = sess.query(WikiPages).filter(WikiPages.repo_id == request.repo_id, WikiPages.level == "module")
            if request.module:
                q = q.filter(WikiPages.module == request.module)
            page = q.first()
        if page is None:
            context.abort(grpc.StatusCode.NOT_FOUND, f"模块页不存在: repo={request.repo_id} module={request.module}")
        return pb2.WikiPage(
            id=page.id, repo_id=page.repo_id, level=page.level, title=page.title,
            content_md=page.content_md, rev=page.rev, stale=page.stale,
        )


def register(server) -> None:
    pb2_grpc.add_WikiBuilderServicer_to_server(WikiBuilderServicer(), server)


def main() -> None:
    setup_logging(NAME)
    log.info("starting %s", NAME)
    run_server(GRPC_PORTS[NAME], NAME, register)


if __name__ == "__main__":
    main()
