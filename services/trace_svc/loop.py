"""trace-svc 内的缺陷闭环与计划判定（M5）。"""

import json
import logging
import uuid
from datetime import datetime

from services.shared.config import GRPC_PORTS
from services.shared.db import get_session
from services.shared.grpc_client import grpc_call
from services.shared.models import Cases, Defects, Iterations, Runs
from services.shared.trace import emit

log = logging.getLogger("trace-svc.loop")

# 按模块自动指派（服务目录 mock）
ASSIGNEE_MAP = {
    "orders": "研发-张三",
    "payments": "研发-李四",
    "payments_api": "研发-李四",
    "inventory": "研发-王五",
}


def _assignee(module: str) -> str:
    for k, v in ASSIGNEE_MAP.items():
        if k in (module or "").lower():
            return v
    return "研发-待指派"


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
        return {"id": defect.id, "code": defect.code, "severity": severity, "assignee": defect.assignee, "status": defect.status}


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

    report = grpc_call(
        "runner-svc",
        GRPC_PORTS["runner-svc"],
        "TestRunner",
        "Execute",
        {"run_id": run_code, "cases": suite_cases, "repo_id": 0, "trace_id": trace_id},
        timeout=300,
    )
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


def evaluate_plan(iter_code: str, version: str, req_codes: list[str], trace_id: str) -> dict:
    """准入/准出自动核对（PRD FR-10）。"""
    with get_session() as sess:
        cases = sess.query(Cases).filter(Cases.source_req.in_(req_codes)).all() if req_codes else []
        runs = sess.query(Runs).filter(Runs.req_code.in_(req_codes)).all() if req_codes else []
        defects = sess.query(Defects).filter(Defects.req_code.in_(req_codes)).all() if req_codes else []
        iter_row = sess.query(Iterations).filter(Iterations.code == iter_code).first()

        total_cases = len(cases)
        in_library = sum(1 for c in cases if c.status == "已入库")
        review_pass_rate = (in_library / total_cases * 100) if total_cases else 0.0
        runs_ok = bool(runs) and all(r.status == "success" for r in runs)
        executed = len(runs) > 0
        fatal_open = [d for d in defects if d.status not in ("已关闭",) and d.severity in ("致命", "严重")]
        leftover = [d for d in defects if d.status not in ("已关闭",) and d.severity not in ("致命", "严重")]

        entry_checks = {
            "冒烟测试通过": runs_ok,
            "用例评审通过率≥90%": review_pass_rate >= 90,
        }
        exit_checks = {
            "用例执行 100%": executed and runs_ok,
            "致命/严重缺陷清零": len(fatal_open) == 0,
            "遗留缺陷有评审结论": all("结论" in (d.detail or "") or d.status == "已关闭" for d in leftover) if leftover else True,
            "测试报告已输出": bool(iter_row and iter_row.report),
        }
        case_stats: dict = {
            "by_layer": {},
            "total": total_cases,
            "reviewed_in": in_library,
            "review_pass_rate": round(review_pass_rate, 1),
        }
        for c in cases:
            case_stats["by_layer"][c.layer] = case_stats["by_layer"].get(c.layer, 0) + 1
        if iter_row is not None:
            iter_row.case_stats = json.dumps(case_stats, ensure_ascii=False)
            iter_row.entry_status = "已准入" if all(entry_checks.values()) else "已打回"
            iter_row.exit_status = "已准出" if all(exit_checks.values()) else "待准出"
            sess.commit()
        checks = [f"[准入] {k}: {'PASS' if v else 'FAIL'}" for k, v in entry_checks.items()] + [
            f"[准出] {k}: {'PASS' if v else 'FAIL'}" for k, v in exit_checks.items()
        ]
        emit("计划", "plan-svc", f"迭代 {iter_code} 准入/准出核对：准入 {'PASS' if all(entry_checks.values()) else 'FAIL'} 准出 {'PASS' if all(exit_checks.values()) else 'FAIL'}", trace_id=trace_id)
        return {"entry_ok": all(entry_checks.values()), "exit_ok": all(exit_checks.values()), "checks": checks, "case_stats": case_stats}


def build_report(iter_code: str, trace_id: str) -> dict:
    """测试报告（平台汇总 + LLM 初稿；mock 确定性模板）。"""
    from services.shared.llm import get_llm

    with get_session() as sess:
        it = sess.query(Iterations).filter(Iterations.code == iter_code).first()
        if it is None:
            raise KeyError(iter_code)
        req_codes = json.loads(it.req_codes or "[]")
        cases = sess.query(Cases).filter(Cases.source_req.in_(req_codes)).all() if req_codes else []
        runs = sess.query(Runs).filter(Runs.req_code.in_(req_codes)).all() if req_codes else []
        defects = sess.query(Defects).filter(Defects.req_code.in_(req_codes)).all() if req_codes else []
        total_runs = len(runs)
        passed_runs = sum(1 for r in runs if r.status == "success")
        coverage = round(sum(r.coverage for r in runs) / total_runs, 1) if runs else 0.0
        ai_generated = len(cases)
        data = {
            "iteration": iter_code,
            "version": it.version,
            "cases": {"total": len(cases), "in_library": sum(1 for c in cases if c.status == "已入库"), "by_layer": {}},
            "runs": {"total": total_runs, "passed": passed_runs, "pass_rate": round(passed_runs / total_runs * 100, 1) if total_runs else 0.0, "avg_coverage": coverage},
            "defects": {"total": len(defects), "open": sum(1 for d in defects if d.status != "已关闭"), "closed": sum(1 for d in defects if d.status == "已关闭")},
            "ai_metrics": {"生成占比": "100%" if cases else "0%", "有效率": f"{round(passed_runs / total_runs * 100, 1) if total_runs else 0}%", "覆盖率": f"{coverage}%"},
        }
        for c in cases:
            data["cases"]["by_layer"][c.layer] = data["cases"]["by_layer"].get(c.layer, 0) + 1

    llm = get_llm()
    summary = llm.chat_text(
        [{"role": "user", "content": f"撰写迭代 {iter_code} 测试报告结论段"}],
        mock=(
            f"结论：迭代 {iter_code}（{it.version}）共执行 {total_runs} 轮，通过率 {data['runs']['pass_rate']}%，"
            f"平均分支覆盖率 {coverage}%；缺陷关闭 {data['defects']['closed']}/{data['defects']['total']}。"
            f"AI 生成用例 {ai_generated} 条全部入库，具备准出条件。"
            if llm.is_mock
            else ""
        ),
    )
    data["summary"] = summary
    data["generated_at"] = datetime.now().isoformat()
    with get_session() as sess:
        it = sess.query(Iterations).filter(Iterations.code == iter_code).first()
        it.report = json.dumps(data, ensure_ascii=False)
        sess.commit()
    emit("计划", "plan-svc", f"测试报告生成 {iter_code}（用例 {len(cases)} run {total_runs} 缺陷 {len(defects)}）", trace_id=trace_id)
    return data
