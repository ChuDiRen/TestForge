"""变更驱动回归：pull 检出函数源码变更 → 关联用例标 stale → 自动回归 → 失败自动建缺陷。

回归沿用用例入库时的测试代码快照（schema_json.code_file），对新检出真实执行：
通过 = 行为兼容（清 stale）；失败 = 行为破坏或用例需更新（保持 stale + 自动建缺陷）。
用例可能来自不同生成批次（不同测试文件），按 code_file 分组逐组执行。
"""

from __future__ import annotations

import json
import logging
import time
import uuid

from services.shared.config import GRPC_PORTS
from services.shared.db import get_session
from services.shared.grpc_client import grpc_call
from services.shared.models import Cases, Runs
from services.shared.trace import emit

log = logging.getLogger("gateway.regression")


def short_code(case_code: str) -> str:
    """CASE-XXXXXX-TC-001 → TC-001（沙箱 junit 名回映射口径）。"""
    if "-TC-" in case_code.upper():
        return "TC-" + case_code.upper().rsplit("-TC-", 1)[-1]
    return case_code


def _code_file_of(schema_json: str) -> str:
    try:
        data = json.loads(schema_json or "{}")
    except json.JSONDecodeError:
        return ""
    cf = data.get("code_file")
    if not cf and isinstance(data.get("schema_json"), str):
        try:
            cf = json.loads(data["schema_json"]).get("code_file")
        except json.JSONDecodeError:
            cf = None
    return str(cf or "")


def mark_stale(repo_id: int, changed_functions: list[str]) -> list[str]:
    """目标函数命中变更集的用例标记 stale，返回受影响 case code（全码）。

    已 stale 的用例一并纳入（上轮回归失败的用例必须随下次变更重新验证，自愈闭环）。
    """
    if not changed_functions:
        return []
    names = set(changed_functions)
    with get_session() as sess:
        rows = sess.query(Cases).filter(Cases.repo_id == repo_id, Cases.status != "已替换").all()
        hit = []
        for c in rows:
            tf = (c.target_function or "").rsplit(".", 1)[-1]
            if tf in names:
                c.stale = True
                hit.append(c.code)
        sess.commit()
    if hit:
        preview = ", ".join(hit[:8]) + ("…" if len(hit) > 8 else "")
        emit("执行", "gateway", f"代码变更影响 {len(hit)} 条用例，已标记待回归: {preview}")
    return hit


def _regress_group(repo_id: int, suite_cases: list[dict], trace_id: str) -> dict:
    """执行同一测试文件内的一组用例，落 run + 更新 stale/last_run_ok + 失败建缺陷。"""
    run_code = f"RUN-{uuid.uuid4().hex[:8].upper()}"
    t0 = time.time()
    report = grpc_call(
        "runner-svc",
        GRPC_PORTS["runner-svc"],
        "TestRunner",
        "Execute",
        {
            "run_id": run_code,
            "cases": suite_cases,
            "repo_id": repo_id,
            "trace_id": trace_id,
            # 同组用例来自同一目标函数（按 code_file 分组的现实约束）；取首个供修复回填定位
            "target_function": next((c.get("target_function") or "" for c in suite_cases), ""),
        },
        timeout=600,
    )
    cost_s = int(time.time() - t0)
    try:
        case_results = json.loads(report.get("log_json") or "{}").get("cases", {})
    except json.JSONDecodeError:
        case_results = {}
    # runner 返回短码（TC-001），套件里是全码（CASE-XXX-TC-001）——按短码对齐；
    # 键缺失说明该用例根本没被执行（文件/选择子问题），同样按失败处理，绝不静默放行
    failed = [c["code"] for c in suite_cases if case_results.get(short_code(c["code"])) != "passed"]
    passed_short = {k for k, v in case_results.items() if v == "passed"}

    with get_session() as sess:
        sess.add(
            Runs(
                code=report.get("run_id") or run_code,
                target="回归:" + ",".join(sorted({(c.get("target_function") or c.get("module") or "") for c in suite_cases}))[:250],
                layer=suite_cases[0]["layer"],
                trigger="代码变更",
                sandbox_status=report.get("sandbox_status", ""),
                pass_total=int(report.get("pass_total") or 0),
                pass_count=int(report.get("pass_count") or 0),
                coverage=float(report.get("coverage") or 0.0),
                repair_rounds=int(report.get("repair_rounds") or 0),
                cost_s=report.get("cost_s") or cost_s,
                status="success" if report.get("status") == "success" else "failed",
                trace_id=trace_id,
                gen_id="",
                log_json=report.get("log_json", ""),
            )
        )
        for c in sess.query(Cases).filter(Cases.code.in_([c["code"] for c in suite_cases])).all():
            sc = short_code(c.code)
            if sc in passed_short:
                c.stale = False
                c.last_run_ok = True
            elif sc in case_results:
                c.last_run_ok = False
        sess.commit()

    if failed:
        try:
            grpc_call(
                "trace-svc",
                GRPC_PORTS["trace-svc"],
                "DefectSvc",
                "CreateFromRun",
                {
                    "run_id": report.get("run_id") or run_code,
                    "case_codes_json": json.dumps(failed, ensure_ascii=False),
                    "req_code": suite_cases[0].get("source_req", ""),
                    "trace_id": trace_id,
                    "reason": f"代码变更回归失败（{len(failed)} 用例）",
                },
                timeout=15,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("regression defect creation failed: %s", exc)

    emit(
        "执行",
        "gateway",
        f"变更回归完成 {report.get('pass_count', 0)}/{report.get('pass_total', 0)} 通过，失败 {len(failed)} 条"
        + ("（已自动建缺陷）" if failed else ""),
        trace_id=trace_id or None,
    )
    return {
        "run_code": report.get("run_id") or run_code,
        "pass_count": int(report.get("pass_count") or 0),
        "pass_total": int(report.get("pass_total") or 0),
        "status": str(report.get("status", "")),
        "failed": failed,
    }


def run_regression(repo_id: int, case_codes: list[str], trace_id: str = "") -> dict:
    """对指定用例执行回归：按测试文件分组逐组执行，汇总结果。"""
    with get_session() as sess:
        rows = sess.query(Cases).filter(Cases.code.in_(case_codes)).all() if case_codes else []
        suite_cases = [
            {
                "code": c.code,
                "title": c.title,
                "layer": c.layer,
                "module": c.module,
                "category": c.category,
                "schema_json": c.schema_json,
                "source_req": c.source_req,
                "target_function": c.target_function,
            }
            for c in rows
        ]
    if not suite_cases:
        return {"run_code": "", "pass_count": 0, "pass_total": 0, "status": "success", "failed": []}

    groups: dict[str, list[dict]] = {}
    for c in suite_cases:
        groups.setdefault(_code_file_of(c["schema_json"]), []).append(c)

    totals = {"pass_count": 0, "pass_total": 0}
    all_failed: list[str] = []
    runs: list[str] = []
    for _cf, group in groups.items():
        res = _regress_group(repo_id, group, trace_id)
        totals["pass_count"] += res["pass_count"]
        totals["pass_total"] += res["pass_total"]
        all_failed.extend(res["failed"])
        if res["run_code"]:
            runs.append(res["run_code"])

    return {
        "run_code": ",".join(runs),
        "pass_count": totals["pass_count"],
        "pass_total": totals["pass_total"],
        "status": "success" if not all_failed else "failed",
        "failed": all_failed,
    }


def regress_changed(repo_id: int, changed_functions: list[str]) -> dict:
    """pull 后入口：标 stale + 回归任务入队（异步执行，结果看 /api/jobs 与 runs）。"""
    from gateway.jobs import enqueue

    hit = mark_stale(repo_id, changed_functions)
    if not hit:
        return {"affected_cases": 0}
    job = enqueue("regression", {"repo_id": repo_id, "case_codes": hit})
    return {"affected_cases": len(hit), "job_code": job["job_code"]}
