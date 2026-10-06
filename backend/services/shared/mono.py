"""单体模式（MONO_MODE=1，默认）：全部服务并入一个进程，gRPC 调用改为进程内直调。

proto 仍作为消息契约（pb2 构造/解析），服务模块边界不变，只是不再走网络。
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("shared.mono")


class MonoRpcError(RuntimeError):
    """进程内直调时 servicer abort 的等价异常。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class FakeContext:
    """servicer 内 context.abort() 的进程内等价物。"""

    def abort(self, code: Any, message: str) -> None:  # noqa: ARG002
        raise MonoRpcError(getattr(code, "name", str(code)), message)


# svc_name(proto 服务名) → servicer 实例
_REGISTRY: dict[str, Any] = {}


def register(svc_name: str, servicer: Any) -> None:
    _REGISTRY[svc_name] = servicer
    log.info("mono: %s registered in-process", svc_name)


def registered() -> list[str]:
    return sorted(_REGISTRY)


def dispatch(svc_name: str, method: str, request: dict[str, Any]) -> Any:
    """进程内调用 servicer 方法，返回原始结果（dict 或生成器）。"""
    inst = _REGISTRY.get(svc_name)
    if inst is None:
        raise MonoRpcError("UNAVAILABLE", f"mono registry missing service: {svc_name}")
    from services.shared.gen import testforge_pb2 as pb2
    from services.shared.grpc_client import _fill, _request_type

    req = _fill(getattr(pb2, _request_type(svc_name, method))(), request)
    return getattr(inst, method)(req, context=FakeContext())


def register_all() -> list[str]:
    """实例化全部 servicer 并注册（应用启动时调用一次）。"""
    from services.contract_registry.main import ContractRegistryServicer
    from services.repo_svc.main import RepoServicer
    from services.req_svc.main import ReqIngestServicer
    from services.runner_svc.main import TestRunnerServicer
    from services.testgen_svc.main import TestGenServicer
    from services.trace_svc.main import DefectServicer, PlanServicer, TraceLogServicer
    from services.wiki_builder.main import WikiBuilderServicer

    register("RepoSvc", RepoServicer())
    register("WikiBuilder", WikiBuilderServicer())
    register("ContractRegistry", ContractRegistryServicer())
    register("ReqIngest", ReqIngestServicer())
    register("TestGen", TestGenServicer())
    register("TestRunner", TestRunnerServicer())
    register("TraceLog", TraceLogServicer())
    register("DefectSvc", DefectServicer())
    register("PlanSvc", PlanServicer())
    return registered()
