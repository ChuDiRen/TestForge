"""trace-svc 入口：gRPC TraceLog（Append/Query 全量实现）+ DefectSvc/PlanSvc。"""

import json
import logging
from datetime import datetime

import grpc

from services.shared.base import pong
from services.shared.config import GRPC_PORTS
from services.shared.db import get_session
from services.shared.gen import testforge_pb2 as pb2
from services.shared.gen import testforge_pb2_grpc as pb2_grpc
from services.shared.grpc_server import run_server
from services.shared.logging import setup_logging
from services.shared.models import TraceEvents
from services.shared.sanitize import sanitize_text

NAME = "trace-svc"
log = logging.getLogger(NAME)


class TraceLogServicer(pb2_grpc.TraceLogServicer):
    def Ping(self, request, context):  # noqa: N802
        return pong(NAME)

    def Append(self, request, context):  # noqa: N802
        ev = request.event
        with get_session() as sess:
            sess.add(
                TraceEvents(
                    trace_id=ev.trace_id,
                    type=ev.type,
                    actor=ev.actor or "system",
                    summary=sanitize_text(ev.summary),
                    req_code=ev.req_code,
                    extra=json.dumps(dict(ev.extra), ensure_ascii=False),
                    ts=datetime.fromtimestamp(ev.ts / 1000) if ev.ts else datetime.now(),
                )
            )
            sess.commit()
        return pb2.Ack(ok=True, message="appended")

    def Query(self, request, context):  # noqa: N802
        with get_session() as sess:
            q = sess.query(TraceEvents)
            if request.trace_id:
                q = q.filter(TraceEvents.trace_id == request.trace_id)
            if request.req_code:
                q = q.filter(TraceEvents.req_code == request.req_code)
            if request.type:
                q = q.filter(TraceEvents.type == request.type)
            rows = q.order_by(TraceEvents.ts.asc(), TraceEvents.id.asc()).limit(request.limit or 200).all()
            for r in rows:
                yield pb2.TraceEvent(
                    trace_id=r.trace_id,
                    type=r.type,
                    actor=r.actor,
                    summary=r.summary,
                    req_code=r.req_code,
                    ts=int(r.ts.timestamp() * 1000),
                )


class DefectServicer(pb2_grpc.DefectSvcServicer):
    def Ping(self, request, context):  # noqa: N802
        return pong(NAME)

    def CreateFromRun(self, request, context):  # noqa: N802
        context.abort(grpc.StatusCode.UNIMPLEMENTED, "M5 上线")

    def TriggerRegression(self, request, context):  # noqa: N802
        context.abort(grpc.StatusCode.UNIMPLEMENTED, "M5 上线")


class PlanServicer(pb2_grpc.PlanSvcServicer):
    def Ping(self, request, context):  # noqa: N802
        return pong(NAME)

    def EvaluateExitPlan(self, request, context):  # noqa: N802
        context.abort(grpc.StatusCode.UNIMPLEMENTED, "M5 上线")


def register(server) -> None:
    pb2_grpc.add_TraceLogServicer_to_server(TraceLogServicer(), server)
    pb2_grpc.add_DefectSvcServicer_to_server(DefectServicer(), server)
    pb2_grpc.add_PlanSvcServicer_to_server(PlanServicer(), server)


def main() -> None:
    setup_logging(NAME)
    log.info("starting %s (TraceLog + DefectSvc + PlanSvc)", NAME)
    run_server(GRPC_PORTS[NAME], NAME, register)


if __name__ == "__main__":
    main()
