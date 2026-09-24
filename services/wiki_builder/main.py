"""wiki-builder 入口：gRPC WikiBuilder。"""

import logging

import grpc

from services.shared.base import pong
from services.shared.config import GRPC_PORTS
from services.shared.gen import testforge_pb2_grpc as pb2_grpc
from services.shared.grpc_server import run_server
from services.shared.logging import setup_logging

NAME = "wiki-builder"
log = logging.getLogger(NAME)


class WikiBuilderServicer(pb2_grpc.WikiBuilderServicer):
    def Ping(self, request, context):  # noqa: N802
        return pong(NAME)

    def Rebuild(self, request, context):  # noqa: N802
        context.abort(grpc.StatusCode.UNIMPLEMENTED, "M1/M2 上线")

    def GetModulePage(self, request, context):  # noqa: N802
        context.abort(grpc.StatusCode.UNIMPLEMENTED, "M2 上线")


def register(server) -> None:
    pb2_grpc.add_WikiBuilderServicer_to_server(WikiBuilderServicer(), server)


def main() -> None:
    setup_logging(NAME)
    log.info("starting %s", NAME)
    run_server(GRPC_PORTS[NAME], NAME, register)


if __name__ == "__main__":
    main()
