"""contract-registry 入口：gRPC ContractRegistry（注册/diff/影响分析）。"""

import logging

import grpc

from services.shared.base import pong
from services.shared.config import GRPC_PORTS
from services.shared.gen import testforge_pb2 as pb2
from services.shared.gen import testforge_pb2_grpc as pb2_grpc
from services.shared.grpc_server import run_server
from services.shared.logutil import setup_logging

NAME = "contract-registry"
log = logging.getLogger(NAME)


class ContractRegistryServicer(pb2_grpc.ContractRegistryServicer):
    def Ping(self, request, context):  # noqa: N802
        return pong(NAME)

    def Register(self, request, context):  # noqa: N802
        from services.contract_registry import service

        res = service.register(
            name=request.name,
            ctype=request.type,
            provider_repo=request.provider_repo,
            version=request.version,
            spec=request.spec,
            consumers=list(request.consumers),
        )
        return pb2.ContractVersion(contract_id=res["contract_id"], version=res["version"], breaking=res["breaking"], changes=res["changes"])

    def Impact(self, request, context):  # noqa: N802
        from services.contract_registry import service

        try:
            events = service.impact(request.contract_id, request.to_v, trace_id=request.trace_id)
        except Exception as exc:  # noqa: BLE001
            log.exception("impact failed")
            context.abort(grpc.StatusCode.INTERNAL, str(exc)[:300])
        for e in events:
            yield pb2.ImpactEvent(
                asset_type=e["asset_type"],
                asset_id=e["asset_id"],
                reason=e["reason"],
                stale_wiki=e["stale_wiki"],
                case_tagged=e["case_tagged"],
            )


def register(server) -> None:
    pb2_grpc.add_ContractRegistryServicer_to_server(ContractRegistryServicer(), server)


def main() -> None:
    setup_logging(NAME)
    log.info("starting %s", NAME)
    run_server(GRPC_PORTS[NAME], NAME, register)


if __name__ == "__main__":
    main()
