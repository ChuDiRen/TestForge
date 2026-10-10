"""trace-svc 平铺 API：追溯事件追加/查询 + 缺陷闭环 + 迭代计划（原 TraceLog/DefectSvc/PlanSvc 契约）。"""

import json

from app.core import health
from app.core.sanitize import sanitize_text


def ping() -> dict:
    return health.ping("trace-svc")


def append(event: dict) -> dict:
    """追加一条追溯事件（已脱敏由调用方 shared.trace 负责）。"""
    from datetime import datetime

    from app.db.session import get_session
    from app.models import TraceEvents

    with get_session() as sess:
        sess.add(
            TraceEvents(
                trace_id=event.get("trace_id", ""),
                type=event.get("type", ""),
                actor=event.get("actor") or "system",
                summary=sanitize_text(event.get("summary", "")),
                req_code=event.get("req_code", ""),
                extra=json.dumps(event.get("extra") or {}, ensure_ascii=False),
                ts=datetime.fromtimestamp(event["ts"] / 1000) if event.get("ts") else datetime.now(),
            )
        )
        sess.commit()
    return {"ok": True, "message": "appended"}


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
        rows = q.order_by(TraceEvents.ts.asc(), TraceEvents.id.asc()).limit(limit or 200).all()
        return [
            {
                "trace_id": r.trace_id,
                "type": r.type,
                "actor": r.actor,
                "summary": r.summary,
                "req_code": r.req_code,
                "ts": int(r.ts.timestamp() * 1000),
            }
            for r in rows
        ]


def create_from_run(run_id: str, case_codes_json: str = "[]", req_code: str = "", trace_id: str = "", reason: str = "") -> dict:
    """失败执行 → 自动建缺陷（四向关联），返回 {id, code, title, status, assignee}。"""
    from app.services.trace import defect_svc

    try:
        case_codes = json.loads(case_codes_json or "[]")
    except json.JSONDecodeError:
        case_codes = []
    res = defect_svc.create_from_run(
        run_id=run_id,
        case_codes=case_codes,
        req_code=req_code,
        trace_id=trace_id,
        reason=reason,
    )
    return {"id": res["id"], "code": res["code"], "title": f"run {run_id} 失败", "status": res["status"], "assignee": res["assignee"]}


def trigger_regression(defect_id: int, trace_id: str = "") -> dict:
    """缺陷定向回归：重跑关联用例 → 通过自动关闭/失败重开，返回 RunReport 形状。"""
    from app.services.trace import defect_svc

    try:
        res = defect_svc.trigger_regression(defect_id, trace_id=trace_id)
    except KeyError as exc:
        raise LookupError(str(exc)) from exc
    return {
        "run_id": res["run_code"],
        "sandbox_status": "regression",
        "pass_total": res["pass_total"],
        "pass_count": res["pass_count"],
        "status": "success" if res["status"] == "已关闭" else "failed",
        "log_json": json.dumps(res, ensure_ascii=False),
    }


def evaluate_plan(code: str, version: str, req_codes: list[str], trace_id: str = "") -> dict:
    """迭代准入/准出核对，返回 {entry_ok, exit_ok, checks, report_json}。"""
    from app.services.trace import plan_svc

    res = plan_svc.evaluate_plan(code, version, list(req_codes), trace_id=trace_id)
    return {
        "entry_ok": res["entry_ok"],
        "exit_ok": res["exit_ok"],
        "checks": res["checks"],
        "report_json": json.dumps(res.get("case_stats", {}), ensure_ascii=False),
    }
