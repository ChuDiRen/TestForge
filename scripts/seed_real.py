"""真实数据种子：以 TestForge 本仓库为示例，全链路真实执行（无 fake）。

流程（全部经 gateway REST，与前端同一路径）：
  ① 接入本仓库（git clone → tree-sitter 索引 → Wiki 编译）
  ② 为真实函数 sanitize_text（services/shared/sanitize.py）两阶段生成 → local 沙箱真跑 pytest
  ③ 录入真实可测需求（日志脱敏）→ 确认生效 → 自动编排生成 → 真实执行
  ④ 建测试计划（准入/准出核对）→ 生成测试报告

前提：gateway 已在 127.0.0.1:8000（make dev）；SANDBOX_MODE=local。
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time

import httpx

GATEWAY = f"http://127.0.0.1:{os.environ.get('GATEWAY_PORT', '8000')}"
_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_URL = (_ROOT).as_uri() if os.name == "nt" else "file:///mnt/e/TestForge"
TARGET = os.environ.get("TF_TARGET", "sanitize_text")

REQ_TITLE = "日志与链路追踪中的敏感凭证必须脱敏"
REQ_BODY = (
    "作为平台维护者，我希望进入日志与 trace 的文本不得泄露任何凭证。\n"
    "验收条件：\n"
    "1. Bearer 令牌必须掩码为 Bearer ***；\n"
    "2. password/token/api_key 键值必须掩码为 <键>=***；\n"
    "3. sk- 开头的密钥必须掩码为 sk-***；\n"
    "4. 不含敏感信息的文本必须原样保留；\n"
    "5. 长度 0 到 10000 字符的输入都不得崩溃，空输入必须返回空字符串；\n"
    "6. 管理员与普通用户的脱敏策略必须一致，任何人不得越权回读原文。"
)

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    CHECKS.append((name, cond, detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def api(method: str, path: str, body: dict | None = None, timeout: float = 120) -> dict:
    r = httpx.request(method, f"{GATEWAY}{path}", json=body, timeout=timeout)
    data = r.json()
    assert data.get("code") == 0, f"{path} -> {data}"
    return data["data"]


def wait_generation(gcode: str, timeout_s: int = 240) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        g = api("GET", f"/api/generations/{gcode}")
        if g["status"] in ("done", "failed"):
            return g
        time.sleep(1.5)
    raise TimeoutError(f"generation {gcode} 超时")


def main() -> int:
    print("== TestForge 真实数据种子（本仓库为示例，SANDBOX_MODE=local）==")
    print(f"  仓库：{REPO_URL}")

    # ① 接入本仓库（真实 clone + 多语言 tree-sitter 索引 + Wiki 编译）
    t0 = time.time()
    repo = api("POST", "/api/repos", {"url": REPO_URL, "branch": "main"})
    rid = repo["id"]
    check("① 本仓库接入（clone→索引→Wiki）", repo["status"] == "已接入", f"id={rid} 耗时{time.time()-t0:.0f}s")
    if repo["status"] != "已接入":
        return 1

    fns = api("GET", f"/api/functions?repo_id={rid}&name={TARGET}")
    fn = next((f for f in fns if f["name"] == TARGET), None)
    check(f"① 索引到真实函数 {TARGET}", fn is not None, f"{fn['module']}.{TARGET} 共索引 {len(api('GET', f'/api/functions?repo_id={rid}'))} 函数")
    if fn is None:
        return 1

    # ② 真实函数 → 两阶段生成 → local 沙箱真跑 pytest
    gen = api("POST", "/api/generations", {"function": TARGET, "repo_id": rid, "layer": "ut"})
    g1 = wait_generation(gen["generation_id"])
    check("② sanitize_text 生成管线完成（plan→guard→codegen→sandbox→coverage）", g1["status"] == "done")

    cases = api("GET", "/api/cases")
    mine = [x for x in cases["items"] if x.get("gen_id") == gen["generation_id"]]
    cats = {x["category"] for x in mine}
    check("② 产出 ≥8 条真实可执行用例", len(mine) >= 8, f"实际 {len(mine)} 类别 {sorted(cats)}")
    check("② 全部真实执行通过（已入库）", bool(mine) and all(x["status"] == "已入库" for x in mine))

    runs = api("GET", "/api/runs")
    run = next((r for r in runs if r["gen_id"] == gen["generation_id"]), None)
    check("② 沙箱为 local 真实执行（非 fake）", run is not None and run["sandbox_status"] == "local",
          f"{run['pass_count']}/{run['pass_total']} 覆盖率={run['coverage']}% 修复={run['repair_rounds']}轮" if run else "")
    check("② 覆盖率来自 coverage.json 真实统计", run is not None and run["coverage"] > 0, f"{run['coverage']}%" if run else "")

    # ③ 真实需求 → 确认生效 → 自动编排 → 真实执行
    req = api("POST", "/api/requirements/ingest", {"title": REQ_TITLE, "body": REQ_BODY, "repo_id": rid})
    check("③ 需求解析（四步管线）G0 通过", req["status"] in ("待人审", "已生效") and req["testability"] >= 80,
          f"{req['code']} 可测性 {req['testability']:.0f}")
    conf = api("POST", f"/api/requirements/{req['id']}/confirm", {"action": "approve"})
    check("③ 确认生效并自动编排", conf["status"] == "已生效", f"目标 {conf['target']} gen={conf['generation_id']}")
    g2 = wait_generation(conf["generation_id"])
    check("③ 需求驱动生成真实完成", g2["status"] == "done")

    # ④ 迭代计划 + 测试报告
    plan = api("POST", "/api/plans", {"version": "real-1", "req_codes": [req["code"]]})
    plans = {p["code"]: p for p in api("GET", "/api/plans")}
    row = plans.get(plan["code"], {})
    check("④ 测试计划创建并核对准入", row.get("entry_status") not in ("", "已打回", None), f"{plan['code']} 准入 {row.get('entry_status')}")
    report = api("POST", f"/api/reports/{plan['code']}")
    check("④ 测试报告生成", bool(report), json.dumps(report, ensure_ascii=False)[:120])

    passed = sum(1 for _, ok_, _ in CHECKS if ok_)
    print(f"== 结果: {passed}/{len(CHECKS)} ==")
    stats = api("GET", "/api/stats/summary")
    print(f"== 当前库：repos={stats['repos']} cases={stats['cases_total']} runs={stats['runs_total']} 通过率={stats['runs_pass_rate']}% wiki={stats['wiki_pages']}页 ==")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
