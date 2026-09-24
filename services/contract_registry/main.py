"""contract-registry 入口：gRPC ContractRegistry。"""

import logging

import grpc

from services.shared.base import pong
from services.shared.config import GRPC_PORTS
from services.shared.gen import testforge_pb2_grpc as pb2_grpc
from services.shared.grpc_server import run_server
from services.shared.logging import setup_logging

NAME = "contract-registry"
log = logging.getLogger(NAME)


class ContractRegistryServicer(pb2_grpc.ContractRegistryServicer):
    def Ping(self, request, context):  # noqa: N802
        return pong(NAME)

    def Register(self, request, context):  # noqa: N802
        context.abort(grpc.StatusCode.UNIMPLEMENTED, "M4 上线")

    def Impact(self, request, context):  # noqa: N802
        context.abort(grpc.StatusCode.UNIMPLEMENTED, "M4 上线")


def register(server) -> None:
    pb2_grpc.add_ContractRegistryServicer_to_server(ContractRegistryServicer(), server)


def main() -> None:
    setup_logging(NAME)
    log.info("starting %s", NAME)
    run_server(GRPC_PORTS[NAME], NAME, register)


if __name__ == "__main__":
    main()
