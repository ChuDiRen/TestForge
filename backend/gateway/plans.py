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
from services.shared.models import Cases, Defects, Iterations, Repos, Runs
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
                    "has_suggestion": bool(d.suggestion),
                    "created_at": d.created_at.isoformat(),
                }
                for d in rows
            ]
        )


@app.get("/api/defects/{defect_id}/suggestion")
def get_defect_suggestion(defect_id: int):
    with get_session() as sess:
        d = sess.get(Defects, defect_id)
        if d is None:
            raise ApiError(404, "缺陷不存在", 404)
        return ok({"code": d.code, "suggestion": d.suggestion})


@app.post("/api/defects/{defect_id}/suggest")
def suggest_defect_fix(defect_id: int):
    """DeepSeek 基于真实失败用例与 pytest 日志产出根因分析与修复建议（Markdown，落库，带缓存）。"""
    from services.shared.llm import LLMError
    from services.shared.llm_cache import chat_cached

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
    """生命周期流转：已确认/修复中/待回归/已关闭。

    关闭时沉淀缺陷教训（LightRAG 增量学习思路）：失败详情 + 已验证的修复建议写入
    rag_documents(kind='lesson')，生成同函数用例时自动召回，同一个坑不踩第二遍。
    """
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
        title, detail, suggestion, case_codes, severity = d.title, d.detail, d.suggestion, d.case_codes, d.severity
        sess.commit()
    emit("缺陷", body.get("actor") or "qa", f"缺陷 {code} → {status}", req_code=req)
    lesson_note = ""
    if status == "已关闭":
        repo_id = 0
        try:
            from services.shared.models import Requirements

            with get_session() as sess:
                if req:
                    row = sess.query(Requirements.repo_id).filter(Requirements.code == req).first()
                    repo_id = int(row[0] or 0) if row and row[0] else 0
                if not repo_id:
                    # 历史需求/缺陷普遍缺 repo 关联：单仓库部署直接归属唯一仓库，
                    # 多仓库且无法判定时才放弃沉淀（lesson 检索按 repo 精确过滤）
                    repo_ids = [r[0] for r in sess.query(Repos.id).all()]
                    if len(repo_ids) == 1:
                        repo_id = int(repo_ids[0])
        except Exception:  # noqa: BLE001
            repo_id = 0
        if repo_id:
            try:
                from services.shared.rag import ensure_rag_documents_table, index_document

                content = (
                    f"# 缺陷 {code}：{title}\n\n严重度：{severity}\n关联需求：{req or '-'}\n关联用例：{case_codes or '-'}\n\n"
                    f"## 失败详情\n{detail[:1500]}\n\n## 修复建议（回归通过后关闭，已验证）\n{suggestion[:1500]}\n\n"
                    f"生成该模块测试用例时应覆盖本缺陷对应的异常分支，避免回归。"
                )
                ensure_rag_documents_table()
                index_document(
                    f"lesson:defect:{code}",
                    "lesson",
                    f"缺陷教训 {code} {title}",
                    content,
                    repo_id=repo_id,
                    meta={"source": "defect-closed", "defect_code": code, "req_code": req},
                )
                lesson_note = f"教训已沉淀（repo {repo_id}，生成时自动召回）"
                emit("知识", "lesson", f"缺陷 {code} 关闭 → 教训入检索库", req_code=req)
            except Exception as exc:  # noqa: BLE001
                lesson_note = f"教训沉淀失败: {exc}"
        # 缺陷本体索引同步刷新（状态/修复建议进语料）
        if repo_id:
            try:
                from services.shared.rag import ensure_rag_documents_table, index_document

                ensure_rag_documents_table()
                index_document(
                    f"defect:{code}",
                    "defect",
                    f"缺陷 {code} {title}",
                    f"缺陷 {code}（{severity}，状态：{status}）\n关联需求：{req or '-'}\n关联用例：{case_codes or '-'}\n失败详情：{detail[:1000]}\n修复建议：{suggestion[:1200]}",
                    repo_id=repo_id,
                    meta={"source": "defect-status", "defect_code": code, "status": status, "severity": severity},
                )
            except Exception:  # noqa: BLE001
                pass
    return ok({"code": code, "status": status, "lesson": lesson_note})


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
    # 测试计划入检索库（kind='plan'）：测试资料五类之一；仓库归属从关联需求反查
    try:
        from services.shared.rag import ensure_rag_documents_table, index_document

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
    """测试报告：平台真实数据汇总，结论段由统计自动生成。"""
    with get_session() as sess:
        from services.shared.models import Iterations

        it = sess.query(Iterations).filter(Iterations.code == iter_code).first()
        if it is None:
            raise ApiError(404, "迭代不存在", 404)
    from services.trace_svc.plan_svc import build_report as _build

    data = _build(iter_code, trace_id="")
    return ok(data)
