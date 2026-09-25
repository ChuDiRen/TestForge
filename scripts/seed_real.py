"""真实数据种子：以 TestForge 本仓库为示例，全链路真实执行（无 fake）。

流程（全部经 gateway REST，与前端同一路径）：
  ① 接入本仓库（git clone → tree-sitter 索引 → Wiki 编译）
  ② 真实函数生成×4：sanitize_text（精选）+ embed / extract_rules / diff_specs
    （探针式特征化：期望值来自对真实代码的实际执行，双跑一致性校验）
  ③ 真实需求×2（日志脱敏 / 规则抽取）→ 确认生效 → 自动编排 → 真实执行
  ④ 注册真实契约（proto 解析 + 运行时 openapi.json）→ 迭代计划 → 测试报告

前提：gateway 已在 127.0.0.1:8000（make dev）；SANDBOX_MODE=local。
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import time

import httpx

GATEWAY = f"http://127.0.0.1:{os.environ.get('GATEWAY_PORT', '8000')}"
_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_URL = _ROOT.as_uri()  # file:///E:/TestForge（Linux: file:///mnt/e/TestForge）

SANITIZE_TARGET = os.environ.get("TF_TARGET", "sanitize_text")
PROBE_TARGETS = ["embed", "extract_rules", "diff_specs"]

REQ_SANITIZE = {
    "title": "日志与链路追踪中的敏感凭证必须脱敏",
    "body": (
        "作为平台维护者，我希望进入日志与 trace 的文本不得泄露任何凭证。\n"
        "验收条件：\n"
        "1. Bearer 令牌必须掩码为 Bearer ***；\n"
        "2. password/token/api_key 键值必须掩码为 <键>=***；\n"
        "3. sk- 开头的密钥必须掩码为 sk-***；\n"
        "4. 不含敏感信息的文本必须原样保留；\n"
        "5. 长度 0 到 10000 字符的输入都不得崩溃，空输入必须返回空字符串；\n"
        "6. 管理员与普通用户的脱敏策略必须一致，任何人不得越权回读原文。"
    ),
}
REQ_RULES = {
    "title": "需求录入必须自动抽取可验证规则",
    "body": (
        "作为QA负责人，我希望需求录入时自动抽取结构化规则。\n"
        "验收条件：\n"
        "1. 用户故事（作为…）必须识别并记录角色；\n"
        "2. 数字约束（不超过/至少/大于等于）必须标记为边界规则；\n"
        "3. 验收条件段落必须完整保留原文证据；\n"
        "4. 任何输入都不得抛出异常，空文本必须返回待确认规则。"
    ),
}

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    CHECKS.append((name, cond, detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def api(method: str, path: str, body: dict | None = None, timeout: float = 120) -> dict:
    """GET 幂等重试：WSL PG 直连偶发瞬时断流时等待自愈，不把抖动当失败。"""
    last: dict = {}
    for attempt in range(3 if method == "GET" else 1):
        r = httpx.request(method, f"{GATEWAY}{path}", json=body, timeout=timeout)
        last = r.json()
        if last.get("code") == 0:
            return last["data"]
        if method == "GET":
            time.sleep(2)
    assert last.get("code") == 0, f"{path} -> {last}"
    return last["data"]


def wait_generation(gcode: str, timeout_s: int = 240) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        g = api("GET", f"/api/generations/{gcode}")
        if g["status"] in ("done", "failed"):
            return g
        time.sleep(1.5)
    raise TimeoutError(f"generation {gcode} 超时")


def run_of(gen_code: str) -> dict | None:
    return next((r for r in api("GET", "/api/runs") if r["gen_id"] == gen_code), None)


def generate_and_verify(name: str, fn_name: str, rid: int, min_cases: int) -> None:
    """生成 → 真实沙箱执行 → 校验（local 模式 + 全通过 + 真实覆盖率）。"""
    gen = api("POST", "/api/generations", {"function": fn_name, "repo_id": rid, "layer": "ut"})
    g = wait_generation(gen["generation_id"])
    check(f"②{name} 生成管线完成", g["status"] == "done")
    cases = [x for x in api("GET", "/api/cases")["items"] if x.get("gen_id") == gen["generation_id"]]
    check(f"②{name} 产出 ≥{min_cases} 条用例", len(cases) >= min_cases, f"实际 {len(cases)}")
    check(f"②{name} 全部真实执行通过", bool(cases) and all(x["status"] == "已入库" for x in cases))
    run = run_of(gen["generation_id"])
    check(f"②{name} local 真实执行 + 真实覆盖率", run is not None and run["sandbox_status"] == "local" and run["coverage"] > 0,
          f"{run['pass_count']}/{run['pass_total']} 覆盖率={run['coverage']}%" if run else "")


def main() -> int:
    print("== TestForge 真实数据种子（本仓库为示例，SANDBOX_MODE=local）==")

    # ① 接入本仓库（真实 clone + 多语言 tree-sitter 索引 + Wiki 编译）
    t0 = time.time()
    repo = api("POST", "/api/repos", {"url": REPO_URL, "branch": "main"})
    rid = repo["id"]
    check("① 本仓库接入（clone→索引→Wiki）", repo["status"] == "已接入", f"id={rid} 耗时{time.time()-t0:.0f}s")
    if repo["status"] != "已接入":
        return 1
    fn_count = len(api("GET", f"/api/functions?repo_id={rid}"))
    check("① 真实函数索引规模 ≥400", fn_count >= 400, f"共 {fn_count} 个函数")

    # ② 真实函数生成：人工精选（sanitize_text）+ 探针特征化（embed/extract_rules/diff_specs）
    fns = api("GET", f"/api/functions?repo_id={rid}&name={SANITIZE_TARGET}")
    check(f"② 索引到真实函数 {SANITIZE_TARGET}", any(f["name"] == SANITIZE_TARGET for f in fns))
    generate_and_verify("sanitize_text", SANITIZE_TARGET, rid, 8)
    for t in PROBE_TARGETS:
        generate_and_verify(t, t, rid, 4)

    # ③ 真实需求 ×2 → 确认生效 → 自动编排 → 真实执行
    req_codes = []
    for label, payload in (("日志脱敏", REQ_SANITIZE), ("规则抽取", REQ_RULES)):
        req = api("POST", "/api/requirements/ingest", {**payload, "repo_id": rid})
        ok_g0 = req["status"] in ("待人审", "已生效") and req["testability"] >= 80
        check(f"③ 需求[{label}] G0 可测性通过", ok_g0, f"{req['code']} 可测性 {req['testability']:.0f}")
        if not ok_g0:
            continue
        conf = api("POST", f"/api/requirements/{req['id']}/confirm", {"action": "approve"})
        check(f"③ 需求[{label}] 确认生效并自动编排", conf["status"] == "已生效", f"目标 {conf['target']}")
        g = wait_generation(conf["generation_id"])
        check(f"③ 需求[{label}] 自动编排真实完成", g["status"] == "done")
        req_codes.append(req["code"])

    # ④ 真实契约注册（proto 解析 + 运行时 openapi）→ 迭代计划 → 测试报告
    proto = (_ROOT / "proto" / "testforge.proto").read_text(encoding="utf-8")
    version = re.search(r'VERSION = "([^"]+)"', (_ROOT / "services" / "shared" / "config.py").read_text(encoding="utf-8")).group(1)
    grpc_spec = {
        "file": "testforge.proto",
        "services": sorted(set(re.findall(r"service\s+(\w+)\s*\{", proto))),
        "rpcs": sorted(set(re.findall(r"rpc\s+(\w+)\s*\(", proto))),
        "messages": sorted(set(re.findall(r"message\s+(\w+)\s*\{", proto))),
    }
    c1 = api("POST", "/api/contracts", {
        "name": "testforge-grpc", "type": "grpc", "provider_repo": "testforge",
        "version": version, "spec": grpc_spec, "consumers": ["gateway"],
    })
    openapi = httpx.get(f"{GATEWAY}/openapi.json", timeout=30).json()
    rest_spec = {"paths": {p: {} for p in openapi.get("paths", {})}}
    c2 = api("POST", "/api/contracts", {
        "name": "testforge-rest-api", "type": "rest", "provider_repo": "testforge",
        "version": version, "spec": rest_spec, "consumers": ["frontend"],
    })
    check("④ 真实契约注册（proto + openapi）", bool(c1.get("contract_id")) and bool(c2.get("contract_id")) and len(grpc_spec["rpcs"]) >= 15,
          f"grpc rpcs={len(grpc_spec['rpcs'])} rest paths={len(rest_spec['paths'])}")

    plan = api("POST", "/api/plans", {"version": "real-1", "req_codes": req_codes})
    plans = {p["code"]: p for p in api("GET", "/api/plans")}
    row = plans.get(plan["code"], {})
    check("④ 测试计划创建并核对准入", row.get("entry_status") not in ("", "已打回", None), f"{plan['code']} 准入 {row.get('entry_status')}")
    report = api("POST", f"/api/reports/{plan['code']}")
    check("④ 测试报告生成", bool(report), json.dumps(report, ensure_ascii=False)[:120])

    passed = sum(1 for _, ok_, _ in CHECKS if ok_)
    print(f"== 结果: {passed}/{len(CHECKS)} ==")
    stats = api("GET", "/api/stats/summary")
    print(f"== 当前库：repos={stats['repos']} cases={stats['cases_total']} runs={stats['runs_total']} 通过率={stats['runs_pass_rate']}% wiki={stats['wiki_pages']}页 contracts={stats['contracts']} ==")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
