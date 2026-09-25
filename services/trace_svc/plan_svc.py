"""trace-svc：迭代计划准入/准出判定 + 测试报告（PlanSvc）。"""

import json
import logging
from datetime import datetime

from services.shared.db import get_session
from services.shared.llm import get_llm
from services.shared.models import Cases, Defects, Iterations, Runs
from services.shared.trace import emit

log = logging.getLogger("trace-svc.plan")


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
