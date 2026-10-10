"""contract-registry 平铺 API：契约注册/影响分析（原 gRPC ContractRegistry 契约）。"""

import logging

from app.core import health

log = logging.getLogger("contract-registry.api")


def ping() -> dict:
    return health.ping("contract-registry")


def register(
    name: str,
    ctype: str = "rest",
    provider_repo: str = "",
    version: str = "v1.0.0",
    spec: str = "",
    consumers: list[str] | None = None,
) -> dict:
    """注册契约（同名=新版本），返回 {contract_id, version, breaking, changes}。"""
    from app.services.contract import service

    return service.register(
        name=name,
        ctype=ctype,
        provider_repo=provider_repo,
        version=version,
        spec=spec,
        consumers=list(consumers or []),
    )


def impact(contract_id: int, to_v: str = "", trace_id: str = "") -> list[dict]:
    """breaking 影响事件列表（原 gRPC 流式，单体下直接返回全量）。"""
    from app.services.contract import service

    try:
        return list(service.impact(contract_id, to_v, trace_id=trace_id))
    except Exception:
        log.exception("impact failed")
        raise
