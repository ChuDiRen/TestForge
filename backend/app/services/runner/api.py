"""runner-svc 平铺 API：沙箱执行 + 修复循环入口（原 gRPC TestRunner.Execute 契约）。"""

import json
import logging
import uuid

from app.core import health

log = logging.getLogger("runner-svc.api")


def ping() -> dict:
    return health.ping("runner-svc")


def execute(
    run_id: str,
    cases: list[dict],
    repo_id: int = 0,
    trace_id: str = "",
    target_function: str = "",
) -> dict:
    """执行测试套件（内部含 ≤3 轮修复循环），返回 RunReport 形状 dict。"""
    from app.services.runner import service

    run_code = run_id or f"RUN-{uuid.uuid4().hex[:8]}"
    # 代码来源：case.schema_json 顶层或嵌套 schema_json 字段中的 "code_file"
    source_code = ""
    for c in cases:
        try:
            data = json.loads(c.get("schema_json") or "")
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        cf = data.get("code_file")
        if not cf and isinstance(data.get("schema_json"), str):
            try:
                cf = json.loads(data["schema_json"]).get("code_file")
            except json.JSONDecodeError:
                cf = None
        if cf:
            source_code = cf
            break
    if not source_code:
        raise ValueError("测试套件缺少 code_file")

    res = service.execute_suite(
        run_code=run_code,
        cases=cases,
        source_code=source_code,
        repo_id=repo_id,
        trace_id=trace_id,
        req_code=cases[0].get("source_req", "") if cases else "",
        # 回归场景：只执行关联用例（case code 尾段 TC-0NN → 函数名子串 tc0nn）
        only=_only_selectors(cases),
        target_function=target_function,
    )
    report = service.result_to_report(run_code, res)
    return {
        "run_id": run_code,
        "sandbox_status": report["sandbox_status"],
        "pass_total": report["pass_total"],
        "pass_count": report["pass_count"],
        "coverage": report["coverage"],
        "repair_rounds": report["repair_rounds"],
        "cost_s": report["cost_s"],
        "status": report["status"],
        "log_json": report["log_json"],
    }


def _only_selectors(cases: list[dict]) -> list[str]:
    """从 case code（CASE-XXXXXX-TC-0NN）提取 pytest -k 选择子串；全量生成时为空。"""
    sel = []
    for c in cases:
        code = ((c.get("code") if isinstance(c, dict) else "") or "").upper()
        if "-TC-" in code:
            sel.append("tc" + code.rsplit("-TC-", 1)[-1].replace("-", ""))
    return sel
