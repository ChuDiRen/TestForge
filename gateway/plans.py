"""缺陷闭环 + 迭代计划 + 测试报告（M5）。"""

import json
import logging
import uuid

from fastapi import Request

from gateway.main import (
    GRPC_PORTS,
    ApiError,
    app,
    get_session,
    grpc_call,
    ok,
)
from services.shared.models import Defects, Iterations
from services.shared.trace import emit

log = logging.getLogger("gateway.loop")


# ---------------- 缺陷闭环（FR-11） ----------------


@app.post("/api/defects")
async def create_defect(request: Request):
    """失败执行自动建缺陷（runner-svc 调用）/ 人工报障。"""
    body = await request.json()
    res = grpc_call(
        "trace-svc",
        GRPC_PORTS["trace-svc"],
        "DefectSvc",
        "CreateFromRun",
        {
            "run_id": body.get("run_id") or body.get("origin_run") or "",
            "case_codes_json": json.dumps(body.get("case_codes") or [], ensure_ascii=False),
            "req_code": body.get("req_code") or "",
            "trace_id": body.get("trace_id") or "",
            "reason": body.get("reason") or "人工报障",
        },
        timeout=15,
    )
    return ok(res)


@app.get("/api/defects")
def list_defects():
    with get_session() as sess:
        rows = sess.query(Defects).order_by(Defects.id.desc()).limit(200).all()
        return ok(
            [
                {
                    "id": d.id,
                    "code": d.code,
                    "title": d.title,
                    "origin_run": d.origin_run,
                    "case_codes": json.loads(d.case_codes or "[]"),
                    "req_code": d.req_code,
                    "severity": d.severity,
                    "status": d.status,
                    "assignee": d.assignee,
                    "trace_id": d.trace_id,
                    "created_at": d.created_at.isoformat(),
                }
                for d in rows
            ]
        )


@app.post("/api/defects/{defect_id}/regression")
def regression_defect(defect_id: int):
    """自动回归：只重跑缺陷关联用例 → 通过自动关闭 / 失败重开。"""
    with get_session() as sess:
        d = sess.get(Defects, defect_id)
        if d is None:
            raise ApiError(404, "缺陷不存在", 404)
        d.status = "修复中"
        sess.commit()
    res = grpc_call(
        "trace-svc",
        GRPC_PORTS["trace-svc"],
        "DefectSvc",
        "TriggerRegression",
        {"id": defect_id},
        timeout=300,
    )
    return ok(res)


@app.post("/api/defects/{defect_id}/status")
async def update_defect_status(defect_id: int, request: Request):
    """生命周期流转：已确认/修复中/待回归/已关闭。"""

    body = await request.json()
    status = body.get("status") or ""
    if status not in ("新建", "已确认", "修复中", "待回归", "已关闭"):
        raise ApiError(1001, "非法状态")
    with get_session() as sess:
        d = sess.get(Defects, defect_id)
        if d is None:
            raise ApiError(404, "缺陷不存在", 404)
        d.status = status
        code, req = d.code, d.req_code
        sess.commit()
    emit("缺陷", body.get("actor") or "qa", f"缺陷 {code} → {status}", req_code=req)
    return ok({"code": code, "status": status})


# ---------------- 迭代计划（FR-10） ----------------


@app.post("/api/plans")
async def create_plan(request: Request):
    """建迭代 + 准入/准出自动判定。"""
    body = await request.json()
    req_codes = body.get("req_codes") or []
    version = body.get("version") or ""
    code = f"ITER-{uuid.uuid4().hex[:6].upper()}"
    with get_session() as sess:
        sess.add(Iterations(code=code, version=version, req_codes=json.dumps(req_codes, ensure_ascii=False), trace_id=""))
        sess.commit()
    check = grpc_call(
        "trace-svc",
        GRPC_PORTS["trace-svc"],
        "PlanSvc",
        "EvaluateExitPlan",
        {"code": code, "version": version, "req_codes": req_codes},
        timeout=30,
    )
    return ok({"code": code, "version": version, "req_codes": req_codes, **check})


@app.get("/api/plans")
def list_plans():
    with get_session() as sess:
        rows = sess.query(Iterations).order_by(Iterations.id.desc()).limit(100).all()
        return ok(
            [
                {
                    "code": i.code,
                    "version": i.version,
                    "req_codes": json.loads(i.req_codes or "[]"),
                    "case_stats": json.loads(i.case_stats or "{}"),
                    "entry_status": i.entry_status,
                    "exit_status": i.exit_status,
                    "has_report": bool(i.report),
                }
                for i in rows
            ]
        )


@app.get("/api/plans/{iter_code}")
def get_plan(iter_code: str):
    with get_session() as sess:
        i = sess.query(Iterations).filter(Iterations.code == iter_code).first()
        if i is None:
            raise ApiError(404, "迭代不存在", 404)
    check = grpc_call(
        "trace-svc",
        GRPC_PORTS["trace-svc"],
        "PlanSvc",
        "EvaluateExitPlan",
        {"code": i.code, "version": i.version, "req_codes": json.loads(i.req_codes or "[]")},
        timeout=30,
    )
    return ok(
        {
            "code": i.code,
            "version": i.version,
            "req_codes": json.loads(i.req_codes or "[]"),
            "case_stats": json.loads(i.case_stats or "{}"),
            "entry_status": i.entry_status,
            "exit_status": i.exit_status,
            "report": json.loads(i.report) if i.report else None,
            "checks": check.get("checks", []),
            "entry_ok": check.get("entry_ok"),
            "exit_ok": check.get("exit_ok"),
        }
    )


@app.post("/api/reports/{iter_code}")
def build_report(iter_code: str):
    """测试报告：平台汇总 + LLM 初稿（mock 确定性）。"""
    with get_session() as sess:
        from services.shared.models import Iterations

        it = sess.query(Iterations).filter(Iterations.code == iter_code).first()
        if it is None:
            raise ApiError(404, "迭代不存在", 404)
    from services.trace_svc.loop import build_report as _build

    data = _build(iter_code, trace_id="")
    return ok(data)
