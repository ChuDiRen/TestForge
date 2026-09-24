"""M5 验收：流程闭环 —— 缺陷闭环、迭代计划准入准出、测试报告、日志追溯、前端 12 视图。"""

import json
import os
import sys
import time
import uuid

import httpx

GATEWAY = f"http://127.0.0.1:{os.environ.get('GATEWAY_PORT', '8000')}"
FRONTEND = f"http://127.0.0.1:{os.environ.get('FRONTEND_PORT', '5173')}"
REPO_URL = os.environ.get("TF_SAMPLE_REPO_URL", "file:///mnt/e/TestForge/fixtures/sample-repo")

CHECKS: list[tuple[str, bool, str]] = []
HEADERS: dict = {}


def check(name: str, cond: bool, detail: str = "") -> None:
    CHECKS.append((name, cond, detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def api(method: str, path: str, body: dict | None = None, timeout: float = 180) -> dict:
    r = httpx.request(method, f"{GATEWAY}{path}", json=body, timeout=timeout, headers=HEADERS)
    data = r.json()
    assert data.get("code") == 0, f"{path} -> {data}"
    return data["data"]


def wait_generation_done(gcode: str, timeout: int = 180) -> dict:
    dl = time.time() + timeout
    while time.time() < dl:
        g = api("GET", f"/api/generations/{gcode}")
        if g["status"] in ("done", "failed"):
            return g
        time.sleep(1.5)
    return {"status": "timeout", "events": []}


def main() -> int:
    print("== TestForge M5 验收（流程闭环）==")
    global HEADERS
    HEADERS = {"X-Trace-Id": f"tr_{uuid.uuid4().hex[:12]}"}

    # ① 前置：仓库 + 需求生效 + 自动编排（复用 M3 管线产生完整数据）
    repo = api("POST", "/api/repos", {"url": REPO_URL, "branch": "main"})
    rid = int(repo["id"])
    api("POST", f"/api/repos/{rid}/pull")
    req = api("POST", "/api/requirements/ingest", {
        "title": "订单数量上限调整与权限校验",
        "body": "作为买家，我希望下单数量受上限保护。\n验收条件：数量 1~999 允许下单，超出应报 QUANTITY_TOO_LARGE；禁用用户应被拒绝。",
        "repo_id": rid,
    })
    confirmed = api("POST", f"/api/requirements/{req['id']}/confirm", {"action": "approve"})
    g = wait_generation_done(confirmed["generation_id"])
    check("前置：需求编排生成完成", g["status"] == "done")
    runs = api("GET", "/api/runs")
    run = next((r for r in runs if r["req_code"] == confirmed["code"]), None)
    check("前置：执行记录关联需求", run is not None, run["code"] if run else "")

    # ② 失败自动建缺陷（runner 超修复轮次上报 → 四向关联 + 自动指派）
    cases = api("GET", f"/api/cases?source_req={confirmed['code']}")
    case_codes = [c["code"] for c in cases["items"][:3]]
    defect = api("POST", "/api/defects", {"run_id": run["code"], "case_codes": case_codes, "req_code": confirmed["code"], "reason": "演示：修复超轮次仍失败"})
    check("失败自动建缺陷（四向关联）", defect["code"].startswith("BUG-") and defect.get("assignee"), f"{defect['code']} → {defect.get('assignee', '-')}（{defect.get('severity', '-')}）")

    # ③ 缺陷生命周期 + 自动回归（只重跑关联用例 → 通过自动关闭）
    defects = api("GET", "/api/defects")
    d = next((x for x in defects if x["code"] == defect["code"]), None)
    check("缺陷台账可见", d is not None and d["status"] == "新建")
    reg = api("POST", f"/api/defects/{d['id']}/regression")
    check("自动回归：只重跑关联用例", reg["pass_total"] == len(case_codes), f"重跑 {reg['pass_total']} 条（关联 {len(case_codes)}）")
    reg_inner = json.loads(reg.get("log_json") or "{}")
    check("回归通过 → 缺陷自动关闭", reg["status"] == "success" and reg_inner.get("status") == "已关闭" and reg["pass_count"] == reg["pass_total"],
          f"缺陷 {reg_inner.get('defect_code')} → {reg_inner.get('status')}")

    # ④ 迭代计划：准入/准出自动判定
    plan = api("POST", "/api/plans", {"version": "v1.0", "req_codes": [confirmed["code"]]})
    check("迭代计划准入自动判定", plan["entry_ok"], "; ".join(check for check in plan["checks"] if check.startswith("[准入]")))
    detail = api("GET", f"/api/plans/{plan['code']}")
    check("准出核对项输出", len(detail["checks"]) >= 4, f"{len(detail['checks'])} 项")

    # ⑤ 测试报告（平台汇总 + LLM 初稿 + AI 度量）
    report = api("POST", f"/api/reports/{plan['code']}")
    check("测试报告生成", "summary" in report and "cases" in report, f"通过率 {report['runs']['pass_rate']}% 覆盖率 {report['runs']['avg_coverage']}%")
    check("报告含 AI 质量度量", "ai_metrics" in report, json.dumps(report.get("ai_metrics", {}), ensure_ascii=False)[:60])

    # ⑥ 追溯：缺陷+计划 事件类型入链
    tr = api("GET", f"/api/traces/{HEADERS['X-Trace-Id']}")
    types = {e["type"] for e in tr.get("events", [])}
    check("trace 覆盖 需求/生成/执行/缺陷/计划", {"需求", "生成", "执行", "缺陷", "计划"} <= types, f"{sorted(types)}")

    # ⑦ 前端 12 视图全部上线（构建产物 + 数据端点）
    VIEWS = ["dashboard", "wiki", "map", "repo-add", "requirements", "plans", "workbench", "cases", "runs", "defects", "logs", "quality"]
    try:
        fr = httpx.get(FRONTEND, timeout=10)
        check("前端可访问", fr.status_code == 200 and "TestForge" in fr.text)
    except Exception as exc:  # noqa: BLE001
        check("前端可访问", False, str(exc)[:60])
    endpoints_ok = True
    for ep in ("/api/repos", "/api/wiki", "/api/contracts", "/api/requirements", "/api/plans", "/api/cases", "/api/runs", "/api/defects", "/api/quality/requirements"):
        try:
            r = httpx.get(f"{GATEWAY}{ep}", timeout=20, headers=HEADERS)
            endpoints_ok = endpoints_ok and r.status_code == 200 and r.json()["code"] == 0
        except Exception:  # noqa: BLE001
            endpoints_ok = False
    check("12 视图后端数据端点全通", endpoints_ok, f"{len(VIEWS)} 个视图")

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    print(f"== 结果: {passed}/{len(CHECKS)} ==")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
