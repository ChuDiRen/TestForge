"""gRPC 服务端辅助：统一 bootstrap + trace 拦截器。"""

import logging
from concurrent import futures
from typing import Callable

import grpc

from services.shared.logging import set_trace_id

log = logging.getLogger("shared.grpc_server")


class TraceServerInterceptor(grpc.ServerInterceptor):
    """从 x-trace-id 元数据恢复链路上下文；无则置空（emit 时自动生成）。"""

    def intercept_service(self, continuation, handler_call_details):  # type: ignore[no-untyped-def]
        metadata = dict(handler_call_details.invocation_metadata or ())
        set_trace_id(metadata.get("x-trace-id") or "")
        return continuation(handler_call_details)


def run_server(port: int, name: str, register: Callable[[grpc.Server], None]) -> None:
    """启动 gRPC 服务器并阻塞；register(server) 完成 servicer 注册。"""
    from services.shared.config import VERSION
    from services.shared.db import db_ok, init_db

    init_db()
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=16), interceptors=[TraceServerInterceptor()])
    register(server)
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    log.info("%s (v%s) listening on :%d db_ok=%s", name, VERSION, port, db_ok())
    server.wait_for_termination()
