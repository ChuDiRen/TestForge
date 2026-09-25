"""req-svc 入口：gRPC ReqIngest（四步解析管线）。"""

import logging

import grpc

from services.shared.base import pong
from services.shared.config import GRPC_PORTS
from services.shared.gen import testforge_pb2 as pb2
from services.shared.gen import testforge_pb2_grpc as pb2_grpc
from services.shared.grpc_server import run_server
from services.shared.llm import get_llm
from services.shared.logutil import setup_logging
from services.shared.trace import emit

NAME = "req-svc"
log = logging.getLogger(NAME)


class ReqIngestServicer(pb2_grpc.ReqIngestServicer):
    def Ping(self, request, context):  # noqa: N802
        return pong(NAME)

    def Parse(self, request, context):  # noqa: N802
        from services.req_svc import parser

        llm = get_llm()
        try:
            res = parser.parse(request.code, request.title, request.body, request.source, request.repo_id, llm)
        except Exception as exc:  # noqa: BLE001
            log.exception("parse failed")
            context.abort(grpc.StatusCode.INTERNAL, str(exc)[:300])
        emit("需求", "req-svc", f"需求解析 {request.code}: 规则 {len(res.rules)} 条 冲突={res.conflict} 可测性={res.testability:.0f}", req_code=request.code, trace_id=request.trace_id or None)
        return pb2.ParseReport(
            req_code=request.code,
            story=res.story,
            rules=[pb2.ExtractedRule(rule=r.text, change=r.change, evidence=r.evidence) for r in res.rules],
            conflict=res.conflict,
            conflict_detail=res.conflict_detail,
            testability=res.testability,
            status="待人审" if res.testability >= 80 else "已打回",
            pipeline=res.pipeline,
            new_rules=res.new_rules_for_wiki,
        )


def register(server) -> None:
    pb2_grpc.add_ReqIngestServicer_to_server(ReqIngestServicer(), server)


def main() -> None:
    setup_logging(NAME)
    log.info("starting %s", NAME)
    run_server(GRPC_PORTS[NAME], NAME, register)


if __name__ == "__main__":
    main()
