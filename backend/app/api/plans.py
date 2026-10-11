"""缺陷闭环 + 迭代计划 + 测试报告（M5）。"""

import json
import logging
import uuid

from fastapi import Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.trace import emit
from app.crud import crud_defects
from app.main import (
    ApiError,
    app,
    get_session,
    ok,
)
from app.models import Cases, Defects, DefectActivity, DefectComments, DefectWorkflow, Iterations, Repos, Requirements, Runs
from app.schemas.defects import DefectCommentIn, DefectCreateIn, DefectUpdateIn
from app.schemas.plans import PlanCreateIn

log = logging.getLogger("app.api.loop")


# ---------------- 缺陷闭环（FR-11） ----------------


@app.post("/api/defects/create")
async def create_defect(data: DefectCreateIn):
    """失败执行自动建缺陷（runner-svc 调用）/ 人工报障。"""
    body = data.model_dump()
    from app.services.trace.api import create_from_run

    res = create_from_run(
        run_id=body.get("run_id") or body.get("origin_run") or "",
        case_codes_json=json.dumps(body.get("case_codes") or [], ensure_ascii=False),
        req_code=body.get("req_code") or "",
        trace_id=body.get("trace_id") or "",
        reason=body.get("reason") or "人工报障",
        title=body.get("title") or "",
    )
    return ok(res)


@app.get("/api/defects")
def list_defects(db: Session = Depends(get_db)):
    rows = db.query(Defects).order_by(Defects.id.desc()).limit(200).all()
    comment_counts = dict(
        db.query(DefectComments.defect_id, func.count(DefectComments.id)).group_by(DefectComments.defect_id).all()
    )
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
                "resolution": d.resolution or "",
                "priority": d.priority or "",
                "fix_version": d.fix_version or "",
                "dupe_of": d.dupe_of or "",
                "assignee": d.assignee,
                "trace_id": d.trace_id,
                "has_suggestion": bool(d.suggestion),
                "comment_count": comment_counts.get(d.id, 0),
                "detail": d.detail or "",
                "updated_at": d.updated_at.isoformat() if d.updated_at else "",
                "created_at": d.created_at.isoformat(),
            }
            for d in rows
        ]
    )


@app.get("/api/defects/workflow")
def get_defect_workflow(db: Session = Depends(get_db)):
    """状态转换图（数据驱动）：前端据此渲染合法流转选项与看板拖拽校验。"""
    rows = db.query(DefectWorkflow).order_by(DefectWorkflow.sort.asc(), DefectWorkflow.id.asc()).all()
    return ok(
        [
            {"from_status": w.from_status, "to_status": w.to_status, "require_comment": bool(w.require_comment)}
            for w in rows
        ]
    )


@app.get("/api/defects/{defect_id}/history")
def defect_history(defect_id: int, db: Session = Depends(get_db)):
    """字段级变更日志（who/field/旧值→新值），Bugzilla bugs_activity 同构。"""
    rows = (
        db.query(DefectActivity)
        .filter(DefectActivity.defect_id == defect_id)
        .order_by(DefectActivity.id.asc())
        .limit(300)
        .all()
    )
    return ok(
        [
            {"actor": a.actor, "field": a.field, "old": a.old_value, "new": a.new_value, "at": a.created_at.isoformat()}
            for a in rows
        ]
    )


@app.get("/api/defects/{defect_id}/comments")
def list_defect_comments(defect_id: int, db: Session = Depends(get_db)):
    rows = (
        db.query(DefectComments)
        .filter(DefectComments.defect_id == defect_id)
        .order_by(DefectComments.id.asc())
        .limit(300)
        .all()
    )
    return ok(
        [{"id": c.id, "who": c.who, "body": c.body, "type": c.type, "at": c.created_at.isoformat()} for c in rows]
    )


@app.post("/api/defects/{defect_id}/comments")
def add_defect_comment(defect_id: int, data: DefectCommentIn, db: Session = Depends(get_db)):
    body = (data.body or "").strip()
    if not body:
        raise ApiError(1001, "评论不能为空")
    d = db.get(Defects, defect_id)
    if d is None:
        raise ApiError(404, "缺陷不存在", 404)
    actor = (data.actor or "qa").strip() or "qa"
    c = DefectComments(defect_id=defect_id, who=actor, body=body, type="normal")
    db.add(c)
    db.commit()
    emit("缺陷", actor, f"缺陷 {d.code} 新增评论（{len(body)} 字）", req_code=d.req_code)
    return ok({"id": c.id, "who": c.who, "body": c.body, "type": c.type, "at": c.created_at.isoformat()})


@app.get("/api/defects/{defect_id}/suggestion")
def get_defect_suggestion(defect_id: int, db: Session = Depends(get_db)):
    d = crud_defects.get(db, defect_id)
    if d is None:
        raise ApiError(404, "缺陷不存在", 404)
    return ok({"code": d.code, "suggestion": d.suggestion})


@app.post("/api/defects/{defect_id}/suggest")
def suggest_defect_fix(defect_id: int):
    """DeepSeek 基于真实失败用例与 pytest 日志产出根因分析与修复建议（Markdown，落库，带缓存）。"""
    from app.services.knowledge.llm import LLMError
    from app.services.knowledge.llm_cache import chat_cached

    with get_session() as sess:
        d = sess.get(Defects, defect_id)
        if d is None:
            raise ApiError(404, "缺陷不存在", 404)
        case_codes = json.loads(d.case_codes or "[]")
        run = sess.query(Runs).filter(Runs.code == d.origin_run).first()
        log_text = ""
        if run is not None and run.log_json:
            try:
                data = json.loads(run.log_json)
                log_text = str(data.get("log", ""))[:3500]
            except json.JSONDecodeError:
                log_text = ""
        cases = sess.query(Cases).filter(Cases.code.in_(case_codes)).all() if case_codes else []
        case_brief = "\n".join(f"- {c.code} {c.title}（{c.category}，目标 {c.target_function or c.module or '未知'}）" for c in cases)
        defect = d

    prompt = (
        f"缺陷 {defect.code}：{defect.title}\n"
        f"严重度：{defect.severity}，状态：{defect.status}\n"
        f"关联失败用例：\n{case_brief or '（无关联用例记录）'}\n\n"
        f"pytest 失败日志（截断）：\n{log_text or '（无日志留存）'}\n\n"
        "请基于以上真实信息输出 Markdown（不要编造日志中不存在的细节）：\n"
        "## 根因分析\n## 修复建议\n## 复测要点"
    )
    try:
        md = chat_cached(
            prompt,
            system="你是资深测试开发工程师，负责失败分诊与根因分析。只依据提供的用例与日志分析，结论必须可追溯到日志证据。",
            role="query",
        )
    except LLMError as exc:
        raise ApiError(503, str(exc), 503) from exc

    with get_session() as sess:
        d = sess.get(Defects, defect_id)
        assert d is not None
        d.suggestion = md
        sess.commit()
    emit("缺陷", "deepseek", f"缺陷 {d.code} 生成 AI 修复建议（{len(md)} 字）", req_code=d.req_code)
    return ok({"code": d.code, "suggestion": md})


@app.post("/api/defects/{defect_id}/regression")
def regression_defect(defect_id: int, db: Session = Depends(get_db)):
    """自动回归：只重跑缺陷关联用例 → 通过自动关闭（结案=已修复）/ 失败重开。"""
    d = crud_defects.get(db, defect_id)
    if d is None:
        raise ApiError(404, "缺陷不存在", 404)
    if d.status != "修复中":
        old_status = d.status
        crud_defects.update(db, db_obj=d, obj_in={"status": "修复中"})
        from app.services.trace.defect_svc import log_activity

        log_activity(defect_id, "plan-svc", "状态", old_status, "修复中")
    from app.services.trace.api import trigger_regression

    res = trigger_regression(defect_id)
    return ok(res)


@app.post("/api/defects/{defect_id}/status")
async def update_defect_status(defect_id: int, data: DefectUpdateIn):
    """通用流转更新（Jira transition screen 语义）：状态转换 + 伴随字段 + 评论一次提交。

    服务端按 defect_workflow 转换图校验（require_comment 边 / 关闭必填结案原因 /
    重开清空结案 / 重复指向扁平化防环），返回字段级 changes {字段: [旧值, 新值]}；
    关闭时自动沉淀缺陷教训（kind='lesson'）入检索库。
    """
    from app.services.trace.defect_svc import DefectConflict, DefectTransitionError, update_defect

    try:
        res = update_defect(defect_id, data.model_dump())
    except KeyError as exc:
        raise ApiError(404, "缺陷不存在", 404) from exc
    except DefectTransitionError as exc:
        raise ApiError(1001, str(exc)) from exc
    except DefectConflict as exc:
        raise ApiError(1002, str(exc), 409) from exc
    return ok(res)


# ---------------- 迭代计划（FR-10） ----------------


def _evaluate(code: str, version: str, req_codes: list[str]) -> dict:
    """准入/准出核对（进程内直调 plan 服务）。"""
    from app.services.trace.api import evaluate_plan

    return evaluate_plan(code, version, req_codes)


@app.post("/api/plans/create")
async def create_plan(data: PlanCreateIn):
    """建迭代 + 准入/准出自动判定。"""
    body = data.model_dump()
    req_codes = body.get("req_codes") or []
    version = body.get("version") or ""
    code = f"ITER-{uuid.uuid4().hex[:6].upper()}"
    with get_session() as sess:
        sess.add(Iterations(code=code, version=version, req_codes=json.dumps(req_codes, ensure_ascii=False), trace_id=""))
        sess.commit()
    check = _evaluate(code, version, req_codes)
    # 测试计划入检索库（kind='plan'）：测试资料五类之一；仓库归属从关联需求反查
    try:
        from app.services.knowledge.rag import ensure_rag_documents_table, index_document

        ensure_rag_documents_table()
        plan_repo = 0
        with get_session() as sess:
            if req_codes:
                row = (
                    sess.query(Requirements.repo_id)
                    .filter(Requirements.code.in_(req_codes), Requirements.repo_id.isnot(None))
                    .first()
                )
                plan_repo = int(row[0] or 0) if row else 0
        if not plan_repo:
            with get_session() as sess:
                ids = [r[0] for r in sess.query(Repos.id).all()]
                plan_repo = int(ids[0]) if len(ids) == 1 else 0
        index_document(
            f"plan:{code}",
            "plan",
            f"测试计划 {code} {version}",
            f"测试计划 {code}\n版本：{version}\n关联需求：{', '.join(req_codes) or '-'}\n"
            f"准入：{'通过' if check.get('entry_ok') else '未通过'} · 准出：{'通过' if check.get('exit_ok') else '未通过'}\n"
            f"检查项：{json.dumps(check.get('checks') or [], ensure_ascii=False)[:1200]}",
            repo_id=plan_repo,
            meta={"source": "plan-created", "plan_code": code, "version": version},
        )
    except Exception:  # noqa: BLE001
        pass
    return ok({"code": code, "version": version, "req_codes": req_codes, **check})


@app.get("/api/plans")
def list_plans(db: Session = Depends(get_db)):
    rows = db.query(Iterations).order_by(Iterations.id.desc()).limit(100).all()
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
def get_plan(iter_code: str, db: Session = Depends(get_db)):
    i = db.query(Iterations).filter(Iterations.code == iter_code).first()
    if i is None:
        raise ApiError(404, "迭代不存在", 404)
    check = _evaluate(i.code, i.version, json.loads(i.req_codes or "[]"))
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
def build_report(iter_code: str, db: Session = Depends(get_db)):
    """测试报告：平台真实数据汇总，结论段由统计自动生成。"""
    it = db.query(Iterations).filter(Iterations.code == iter_code).first()
    if it is None:
        raise ApiError(404, "迭代不存在", 404)
    from app.services.trace.plan_svc import build_report as _build

    data = _build(iter_code, trace_id="")
    return ok(data)
