"""gRPC 客户端辅助：带 trace_id 元数据的通用调用（小封装，避免每处手写 channel）。"""

import json
import logging
from typing import Any

import grpc

from services.shared.config import get_settings
from services.shared.logging import get_trace_id

log = logging.getLogger("shared.grpc")

_STUB_CACHE: dict[str, Any] = {}


def _metadata() -> list[tuple[str, str]]:
    return [("x-trace-id", get_trace_id())]


def grpc_call(service: str, port: int, svc_name: str, method: str, request: dict[str, Any], timeout: float | None = None) -> dict:
    """按服务名调用 RPC，返回 dict（JSON 透传，避免各处依赖消息类型导入）。"""
    from services.shared.gen import testforge_pb2 as pb2
    from services.shared.gen import testforge_pb2_grpc as pb2_grpc

    settings = get_settings()
    timeout = timeout or settings.grpc_timeout_s
    host = settings.service_host or service  # SVC_HOST 为空时按服务名解析（compose 网络）
    target = f"{host}:{port}"
    key = f"{svc_name}:{target}"
    if key not in _STUB_CACHE:
        channel = grpc.insecure_channel(target)
        stub_cls = getattr(pb2_grpc, f"{svc_name}Stub")
        _STUB_CACHE[key] = (channel, stub_cls(channel))
    _, stub = _STUB_CACHE[key]

    req_type = getattr(pb2, _request_type(svc_name, method))
    req = _fill(req_type(), request)
    resp = getattr(stub, method)(req, timeout=timeout, metadata=_metadata())
    return _to_dict(resp)


def grpc_stream(service: str, port: int, svc_name: str, method: str, request: dict[str, Any], timeout: float | None = None):
    """服务端流式 RPC：逐条 yield dict。"""
    from services.shared.gen import testforge_pb2 as pb2
    from services.shared.gen import testforge_pb2_grpc as pb2_grpc

    settings = get_settings()
    timeout = timeout or settings.grpc_timeout_s
    host = settings.service_host or service
    target = f"{host}:{port}"
    key = f"{svc_name}:{target}"
    if key not in _STUB_CACHE:
        channel = grpc.insecure_channel(target)
        stub_cls = getattr(pb2_grpc, f"{svc_name}Stub")
        _STUB_CACHE[key] = (channel, stub_cls(channel))
    _, stub = _STUB_CACHE[key]

    req_type = getattr(pb2, _request_type(svc_name, method))
    req = _fill(req_type(), request)
    for msg in getattr(stub, method)(req, timeout=timeout, metadata=_metadata()):
        yield _to_dict(msg)


def _request_type(svc_name: str, method: str) -> str:
    return _REQ_MAP.get((svc_name, method), "PingReq")


_REQ_MAP: dict[tuple[str, str], str] = {
    ("RepoSvc", "Register"): "RepoSpec",
    ("RepoSvc", "Pull"): "PullReq",
    ("WikiBuilder", "Rebuild"): "RepoDiff",
    ("WikiBuilder", "GetModulePage"): "PageQuery",
    ("ContractRegistry", "Register"): "Contract",
    ("ContractRegistry", "Impact"): "ContractDiff",
    ("TestGen", "Generate"): "GenPlan",
    ("TestGen", "RegenerateAffected"): "Scope",
    ("TestRunner", "Execute"): "TestSuite",
    ("ReqIngest", "Parse"): "RequirementDoc",
    ("TraceLog", "Append"): "AppendReq",
    ("TraceLog", "Query"): "TraceQuery",
    ("DefectSvc", "CreateFromRun"): "RunFailure",
    ("DefectSvc", "TriggerRegression"): "DefectId",
    ("PlanSvc", "EvaluateExitPlan"): "Iteration",
}


def _fill(req: Any, data: dict[str, Any]) -> Any:
    for k, v in data.items():
        if not hasattr(req, k):
            continue
        cur = getattr(req, k)
        if isinstance(v, dict):
            _fill(cur, v)
        elif isinstance(v, list):
            if v and isinstance(v[0], dict):
                for item in v:
                    _fill(cur.add(), item)
            else:
                cur.extend(v)
        else:
            try:
                setattr(req, k, v)
            except TypeError:
                setattr(req, k, json.dumps(v, ensure_ascii=False))
    return req


def _to_dict(msg: Any) -> dict:
    from google.protobuf.json_format import MessageToDict

    d = MessageToDict(msg, preserving_proto_field_name=True)
    return d
