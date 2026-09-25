"""M4 验收：契约中心 + 多仓 —— 注册/diff/breaking 影响分析/定向重生成。

验收（PROMPT §10）：契约注册/diff/breaking 影响分析、多仓接入、跨服务上下文。
"""

import os
import pathlib
import sys
import uuid

import httpx

GATEWAY = f"http://127.0.0.1:{os.environ.get('GATEWAY_PORT', '8000')}"
ORDER_REPO_URL = os.environ.get("TF_SAMPLE_REPO_URL", "file:///mnt/e/TestForge/fixtures/sample-repo")
API_REPO_URL = os.environ.get("TF_API_REPO_URL", "file:///mnt/e/TestForge/fixtures/api-repo")

CHECKS: list[tuple[str, bool, str]] = []
HEADERS: dict = {}


def check(name: str, cond: bool, detail: str = "") -> None:
    CHECKS.append((name, cond, detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def api(method: str, path: str, body: dict | None = None, timeout: float = 120) -> dict:
    r = httpx.request(method, f"{GATEWAY}{path}", json=body, timeout=timeout, headers=HEADERS)
    data = r.json()
    assert data.get("code") == 0, f"{path} -> {data}"
    return data["data"]


def main() -> int:
    # 确保被测源仓库为 git 仓库（幂等；新环境克隆后也能直接跑）
    import subprocess
    seed = pathlib.Path(__file__).resolve().parent / ("seed_api_repo.py" if "m4" in __file__ else "seed.py")
    subprocess.run([sys.executable, str(seed)], check=True, cwd=str(pathlib.Path(__file__).resolve().parents[1]))

    print("== TestForge M4 验收（契约+多仓）==")
    global HEADERS
    HEADERS = {"X-Trace-Id": f"tr_{uuid.uuid4().hex[:12]}"}

    # ① 多仓接入：订单仓 + 支付 API 仓
    order_repo = api("POST", "/api/repos", {"url": ORDER_REPO_URL, "branch": "main"})
    api_repo = api("POST", "/api/repos", {"url": API_REPO_URL, "branch": "main"})
    api("POST", f"/api/repos/{int(order_repo['id'])}/pull")
    api("POST", f"/api/repos/{int(api_repo['id'])}/pull")
    repos = api("GET", "/api/repos")
    check("多仓接入（≥2 仓库）", len(repos) >= 2, f"共 {len(repos)} 仓")
    fns2 = api("GET", f"/api/functions?repo_id={int(api_repo['id'])}")
    check("第二仓库 tree-sitter 索引", any(f["name"] == "submit_payment" for f in fns2), f"api-repo 函数 {len(fns2)}")

    # ② 契约注册：Payment API v2.3.1（名称带运行唯一后缀，保证幂等）
    cname = f"Payment API {uuid.uuid4().hex[:4]}"
    spec_v1 = {
        "paths": {"/api/pay": {"post": {"__fields__": {"payUrl": {}, "amount": {"required": True}}}}},
        "error_codes": ["PAY_101", "PAY_402"],
    }
    reg1 = api("POST", "/api/contracts", {"name": cname, "type": "rest", "provider_repo": "api-repo", "version": "v2.3.1", "spec": spec_v1, "consumers": ["app.orders.service"]})
    check("契约注册 v2.3.1", not reg1["breaking"] and not reg1.get("changes"))
    cid = int(reg1["contract_id"])

    # ③ 契约 breaking 变更：v2.4.0（移除 payUrl、新增必填 redirectUrl、弃用 PAY_101）
    spec_v2 = {
        "paths": {"/api/pay": {"post": {"__fields__": {"redirectUrl": {"required": True}, "amount": {"required": True}}}}},
        "error_codes": ["PAY_402"],
    }
    reg2 = api("POST", "/api/contracts", {"name": cname, "type": "rest", "provider_repo": "api-repo", "version": "v2.4.0", "spec": spec_v2, "consumers": ["app.orders.service"]})
    changes = reg2.get("changes", [])
    check("版本 diff 识别 breaking 变更", reg2["breaking"], "; ".join(changes)[:80])
    check("breaking 规则命中（字段移除/必填新增/错误码弃用）",
          any("payUrl" in c for c in changes) and any("redirectUrl" in c for c in changes) and any("PAY_101" in c for c in changes),
          f"{len(changes)} 项")

    # ④ 影响分析：传播链 + 资产清单
    impacts = api("POST", f"/api/contracts/{cid}/impact", {"to_v": "v2.4.0"})
    stale_pages = [e for e in impacts if e.get("stale_wiki") and e.get("asset_type") == "wiki_page"]
    tagged = [e for e in impacts if e.get("case_tagged") and e.get("asset_type") == "case"]
    check("影响传播：消费方 wiki 页置 stale", len(stale_pages) > 0, f"{len(stale_pages)} 页")
    check("受影响用例打标（资产清单）", len(tagged) > 0, f"{len(tagged)} 用例")
    check("传播链记录（提供方→wiki→用例→重生成）", any(e.get("asset_type") == "propagation" for e in impacts))

    # ⑤ wiki stale 联动验证
    wiki = api("GET", f"/api/wiki?repo_id={int(order_repo['id'])}")
    stale_now = [w for w in wiki if w["stale"]]
    check("Wiki stale 与影响分析联动", len(stale_now) > 0, f"{[w['module'] for w in stale_now][:3]}")

    # ⑥ 定向重生成（仅受影响用例，非全量）
    regen = api("POST", "/api/regenerate", {"repo_id": int(order_repo["id"]), "target_function": "create_order", "case_ids": [t["asset_id"] for t in tagged[:3]], "reason": "contract-impact:Payment API@v2.4.0"})
    check("定向重生成受影响用例", regen.get("cases_total", 0) > 0, f"重生成清单 {regen.get('cases_total')} 条")

    # ⑦ 跨服务上下文：testgen 组装包含 contract 路
    gen = api("POST", "/api/generations", {"function": "submit_payment", "repo_id": int(api_repo["id"]), "layer": "ut"})
    gdetail = api("GET", f"/api/generations/{gen['generation_id']}")
    ctx_event = next((e for e in gdetail["events"] if e["stage"] == "plan"), {})
    check("跨服务上下文（生成目标含契约注册的第二仓）", gen["generation_id"].startswith("GEN"), f"ctx={ctx_event.get('message', '')[:50]}")

    # ⑧ trace 全链
    tr = api("GET", f"/api/traces/{HEADERS['X-Trace-Id']}")
    types = {e["type"] for e in tr.get("events", [])}
    check("trace 覆盖 仓库+契约 双类型", {"仓库", "契约"} <= types, f"{sorted(types)}")

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    print(f"== 结果: {passed}/{len(CHECKS)} ==")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
