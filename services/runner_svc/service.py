"""runner-svc 业务：Execute（沙箱执行 + 修复循环 ≤3 轮 + 覆盖率回填）。"""

import json
import logging
import time
from pathlib import Path

from services.runner_svc import sandbox
from services.shared.config import GRPC_PORTS
from services.shared.db import get_session
from services.shared.grpc_client import grpc_call
from services.shared.logutil import get_trace_id
from services.shared.models import Repos
from services.shared.trace import emit

log = logging.getLogger("runner-svc")

MAX_REPAIR_ROUNDS = 3


def _repo_checkout(repo_id: int) -> Path:
    with get_session() as sess:
        repo = sess.get(Repos, repo_id) if repo_id else None
        if repo is not None and repo.local_path:
            return Path(repo.local_path)
    # 未注册仓库时退回 fixtures/sample-repo（demo 直跑）
    return Path("fixtures/sample-repo")


def execute_suite(run_code: str, cases: list[dict], source_code: str, repo_id: int, trace_id: str, req_code: str, trigger: str = "手动", only: list[str] | None = None, cov_pkg: str = "") -> dict:
    """执行闭环：写 workspace → 沙箱执行 → 失败修复循环 ≤3 → 覆盖率。返回 run 记录 dict。"""
    t0 = time.time()
    checkout = _repo_checkout(repo_id)
    ws = sandbox.prepare_workspace(run_code, checkout)
    filename = f"test_tf_gen_{run_code.lower()}.py"
    sandbox.write_test_file(ws, filename, source_code)

    timeline: list[dict] = []
    res = sandbox.execute(run_code, ws, [filename], only, cov_pkg=cov_pkg or _cov_pkg(cases))
    timeline.append({"round": 0, "status": res.status, "pass": f"{res.pass_count}/{res.pass_total}", "mode": res.mode})

    rounds = 0
    while res.status != "success" and res.failures and rounds < MAX_REPAIR_ROUNDS:
        rounds += 1
        failed_codes = [f["case"] for f in res.failures]
        emit("执行", "runner-svc", f"{run_code} 第 {rounds} 轮修复：失败 {len(failed_codes)} 条回填重生成", req_code=req_code, trace_id=trace_id or None)
        try:
            grpc_call(
                "testgen-svc",
                GRPC_PORTS["testgen-svc"],
                "TestGen",
                "RegenerateAffected",
                {
                    "repo_id": repo_id,
                    "case_ids": failed_codes,
                    "trace_id": trace_id or get_trace_id(),
                    "reason": f"repair:{rounds}",
                    "target_function": _target_of(cases),
                },
                timeout=30,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("repair regenerate failed: %s", exc)
        # 重写测试文件（mock 下为幂等重渲染，等价修复）
        res = sandbox.execute(run_code, ws, [filename], only, cov_pkg=cov_pkg or _cov_pkg(cases))
        timeline.append({"round": rounds, "status": res.status, "pass": f"{res.pass_count}/{res.pass_total}", "mode": res.mode})

    cost = int(time.time() - t0)
    emit(
        "执行",
        "runner-svc",
        f"{run_code} 执行{'通过' if res.status == 'success' else '失败'} {res.pass_count}/{res.pass_total} 覆盖率={res.coverage}% 修复={rounds}轮 sandbox={res.mode}",
        req_code=req_code,
        trace_id=trace_id or None,
    )
    return {
        "run_code": run_code,
        "sandbox_status": res.mode,
        "pass_total": res.pass_total,
        "pass_count": res.pass_count,
        "coverage": res.coverage,
        "repair_rounds": rounds,
        "cost_s": cost,
        "status": res.status,
        "cases": res.cases,
        "failures": res.failures,
        "timeline": timeline,
        "log": res.log,
    }


def _target_of(cases: list[dict]) -> str:
    for c in cases:
        tf = c.get("target_function") or ""
        if tf:
            return tf.split(".")[-1]
    return ""


def _cov_pkg(cases: list[dict]) -> str:
    """覆盖率统计目标：优先用例 module 全路径（只统计被测模块，口径精确）。"""
    for c in cases:
        mod = str(c.get("module") or "")
        if mod:
            return mod
    for c in cases:
        tf = str(c.get("target_function") or "")
        if "." in tf:
            return tf.rsplit(".", 1)[0]
    name = _target_of(cases)
    if not name:
        return ""
    from services.shared.models import Functions

    with get_session() as sess:
        row = sess.query(Functions).filter(Functions.name == name).first()
        if row is not None and row.module:
            return row.module
    return ""


def result_to_report(run_code: str, res: dict) -> dict:
    """RunReport 兼容 dict（log_json 序列化用）。"""
    return {
        "run_id": run_code,
        "sandbox_status": res["sandbox_status"],
        "pass_total": res["pass_total"],
        "pass_count": res["pass_count"],
        "coverage": res["coverage"],
        "repair_rounds": res["repair_rounds"],
        "cost_s": res["cost_s"],
        "status": res["status"],
        "log_json": json.dumps({"timeline": res["timeline"], "cases": res["cases"], "failures": res["failures"], "log": res["log"][:2000]}, ensure_ascii=False),
    }
