"""traceID 全链路透传 + 事件落库（进程内直调 trace-svc API）。

入库前统一过脱敏钩子（sanitize），保证 token/密码等不进审计日志。
"""

import json
import logging
from datetime import datetime

from app.core.logutil import get_trace_id, new_trace_id
from app.core.sanitize import sanitize_text

log = logging.getLogger("shared.trace")

TYPES = ("需求", "生成", "执行", "仓库", "契约", "缺陷", "计划", "认证")


def emit(
    trace_type: str,
    actor: str,
    summary: str,
    req_code: str = "",
    trace_id: str | None = None,
    extra: dict[str, str] | None = None,
) -> str:
    """追加一条追溯事件；返回 trace_id。落库失败仅告警，不阻塞主流程。"""
    tid = trace_id or get_trace_id()
    if tid in ("", "-"):
        tid = new_trace_id()
    payload: dict = {
        "trace_id": tid,
        "type": trace_type if trace_type in TYPES else "生成",
        "actor": actor,
        "summary": sanitize_text(summary),
        "req_code": req_code or "",
        "ts": int(datetime.now().timestamp() * 1000),
        "extra": {k: sanitize_text(v) for k, v in (extra or {}).items()},
    }
    try:
        from app.services.trace import api as trace_api

        trace_api.append(payload)
    except Exception as exc:  # noqa: BLE001
        log.warning("trace event append failed (%s)，事件丢弃: %s", exc, payload["summary"][:80])
    return tid


def query(trace_id: str = "", req_code: str = "", trace_type: str = "", limit: int = 200) -> list[dict]:
    from app.db.session import get_session
    from app.models import TraceEvents

    with get_session() as sess:
        q = sess.query(TraceEvents)
        if trace_id:
            q = q.filter(TraceEvents.trace_id == trace_id)
        if req_code:
            q = q.filter(TraceEvents.req_code == req_code)
        if trace_type:
            q = q.filter(TraceEvents.type == trace_type)
        rows = q.order_by(TraceEvents.ts.asc(), TraceEvents.id.asc()).limit(limit).all()
        return [
            {
                "ts": r.ts.isoformat(),
                "trace_id": r.trace_id,
                "type": r.type,
                "actor": r.actor,
                "summary": r.summary,
                "req_code": r.req_code,
                "extra": json.loads(r.extra or "{}"),
            }
            for r in rows
        ]
