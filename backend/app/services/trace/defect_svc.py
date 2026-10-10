"""trace-svc：缺陷闭环 —— 失败自动建缺陷（CreateFromRun）+ 定向回归（TriggerRegression）。"""

import json
import logging
import uuid

from app.core.trace import emit
from app.db.session import get_session
from app.models import Cases, Defects, Runs

log = logging.getLogger("trace-svc.defect")

# 平台没有真实人员目录，不编造指派对象——缺陷默认未指派，由 QA 在缺陷页人工流转
DEFAULT_ASSIGNEE = "未指派"


def _assignee(module: str) -> str:
    return DEFAULT_ASSIGNEE


def create_from_run(run_id: str, case_codes: list[str], req_code: str, trace_id: str, reason: str) -> dict:
    """失败执行 → 自动建缺陷（四向关联 run/用例/需求/traceID + 自动指派）。"""
    with get_session() as sess:
        run = sess.query(Runs).filter(Runs.code == run_id).first()
        module = ""
        if run is not None:
            module = run.target.split(".")[0] if "." in run.target else ""
        case_rows = sess.query(Cases).filter(Cases.code.in_(case_codes)).all() if case_codes else []
        if case_rows:
            module = case_rows[0].target_function.split(".")[0] if case_rows[0].target_function else module
        severity = "致命" if len(case_codes) > 5 else "严重" if case_codes else "一般"
        defect = Defects(
            code=f"BUG-{uuid.uuid4().hex[:6].upper()}",
            title=f"run {run_id} 执行失败（{reason[:60] or '超修复轮次'}）",
            origin_run=run_id,
            case_codes=json.dumps(case_codes, ensure_ascii=False),
            req_code=req_code,
            severity=severity,
            status="新建",
            assignee=_assignee(module),
            trace_id=trace_id,
            detail=json.dumps({"failed": len(case_codes), "module": module}, ensure_ascii=False),
        )
        sess.add(defect)
        sess.commit()
        emit("缺陷", "runner-svc", f"失败自动建缺陷 {defect.code}（{severity}，指派 {defect.assignee}）关联 run={run_id} 用例={len(case_codes)}", req_code=req_code, trace_id=trace_id)
        # 缺陷入检索库（kind='defect'）：生成上下文 bugs 检索路与全局搜索的语料源
        try:
            from app.services.knowledge.rag import ensure_rag_documents_table, index_document

            ensure_rag_documents_table()
            index_document(
                f"defect:{defect.code}",
                "defect",
                f"缺陷 {defect.code} {defect.title}",
                f"缺陷 {defect.code}（{severity}，状态：新建）\n关联 run：{run_id}\n关联用例：{', '.join(case_codes) or '-'}\n关联需求：{req_code or '-'}\n失败原因：{reason}\n详情：{defect.detail[:1200]}",
                repo_id=_infer_repo_id(req_code),
                meta={"source": "defect-created", "defect_code": defect.code, "status": defect.status, "severity": severity},
            )
        except Exception:  # noqa: BLE001
            log.warning("缺陷 %s 检索索引失败（不阻塞建单）", defect.code, exc_info=True)
        return {"id": defect.id, "code": defect.code, "severity": severity, "assignee": defect.assignee, "status": defect.status}


def _infer_repo_id(req_code: str) -> int:
    """缺陷没有直接 repo 关联：经需求反查；单仓库部署兜底取唯一仓库（与 lesson 沉淀同规则）。"""
    from app.models import Repos, Requirements

    with get_session() as sess:
        if req_code:
            row = sess.query(Requirements.repo_id).filter(Requirements.code == req_code).first()
            if row and row[0]:
                return int(row[0])
        ids = [r[0] for r in sess.query(Repos.id).all()]
        return int(ids[0]) if len(ids) == 1 else 0


def trigger_regression(defect_id: int, trace_id: str) -> dict:
    """自动回归：只重跑缺陷关联用例 → 通过自动关闭 / 失败重开。"""
    with get_session() as sess:
        d = sess.get(Defects, defect_id)
        if d is None:
            raise KeyError(f"defect {defect_id} not found")
        case_codes = json.loads(d.case_codes or "[]")
        case_rows = sess.query(Cases).filter(Cases.code.in_(case_codes)).all() if case_codes else []
        code_file = ""
        for c in case_rows:
            try:
                data = json.loads(c.schema_json or "{}")
            except json.JSONDecodeError:
                continue
            # 入库 schema 为 suite_cases 元素：code_file 在顶层或嵌套 schema_json 字段
            cf = data.get("code_file")
            if not cf and isinstance(data.get("schema_json"), str):
                try:
                    cf = json.loads(data["schema_json"]).get("code_file")
                except json.JSONDecodeError:
                    cf = None
            if cf:
                code_file = cf
                break
        if not case_rows or not code_file:
            d.status = "已关闭"
            d.trace_id = d.trace_id or trace_id
            sess.commit()
            emit("缺陷", "plan-svc", f"缺陷 {d.code} 无可回归用例，按遗留结论关闭", req_code=d.req_code, trace_id=trace_id)
            return {"run_code": "-", "pass_count": 0, "pass_total": 0, "status": "已关闭", "defect_code": d.code}

        suite_cases = [
            {
                "code": c.code,
                "title": c.title,
                "layer": c.layer,
                "module": c.module,
                "category": c.category,
                "schema_json": c.schema_json,
                "source_req": c.source_req,
            }
            for c in case_rows
        ]
        run_code = f"RUN-{uuid.uuid4().hex[:8].upper()}"

    from app.services.runner.api import execute as runner_execute

    report = runner_execute(run_code, suite_cases, repo_id=0, trace_id=trace_id)
    passed = int(report.get("pass_count") or 0)
    total = int(report.get("pass_total") or 0)
    with get_session() as sess:
        d = sess.get(Defects, defect_id)
        d.status = "已关闭" if passed == total and total > 0 else "待回归"
        sess.commit()
    emit(
        "缺陷",
        "plan-svc",
        f"缺陷回归 {d.code}: 重跑关联用例 {passed}/{total} → {d.status}",
        req_code=d.req_code,
        trace_id=trace_id,
    )
    return {"run_code": report.get("run_id", run_code), "pass_count": passed, "pass_total": total, "status": d.status, "defect_code": d.code}
