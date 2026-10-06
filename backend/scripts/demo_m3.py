"""M3 验收：需求+RAG —— 四步解析管线、可测性评分 G0 打回、pgvector 相似用例检索。

验收（PROMPT §10）：需求解析四步管线、可测性评分打回、pgvector 相似用例检索。
"""

import os
import pathlib
import sys
import time
import uuid

import httpx
from _auth import auth_headers

GATEWAY = f"http://127.0.0.1:{os.environ.get('GATEWAY_PORT', '8000')}"
_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_URL = os.environ.get(
    "TF_SAMPLE_REPO_URL",
    (_ROOT / "fixtures" / "sample-repo").as_uri() if os.name == "nt" else "file:///mnt/e/TestForge/fixtures/sample-repo",
)

CHECKS: list[tuple[str, bool, str]] = []
HEADERS: dict = {}


def check(name: str, cond: bool, detail: str = "") -> None:
    CHECKS.append((name, cond, detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def api(method: str, path: str, body: dict | None = None, timeout: float = 120) -> dict:
    r = httpx.request(method, f"{GATEWAY}{path}", json=body, timeout=timeout, headers={**auth_headers(), **HEADERS})
    data = r.json()
    assert data.get("code") == 0, f"{path} -> {data}"
    return data["data"]


GOOD_REQ = {
    "title": "订单数量上限调整与权限校验",
    "source": "paste",
    "body": (
        "作为买家，我希望下单数量受上限保护以防止误操作。\n"
        "验收条件：\n"
        "1. 数量 1~999 允许下单，超出应报 QUANTITY_TOO_LARGE；\n"
        "2. 禁用用户与 guest 角色下单应被权限校验拒绝；\n"
        "3. 数量上限 999 保持不变。"
    ),
}
CONFLICT_REQ = {
    "title": "下单数量上限调整 999 → 500",
    "source": "paste",
    "body": (
        "作为风控负责人，我希望把下单数量上限 999 → 500，以降低大额误单风险。\n"
        "验收条件：上限 500 生效后，数量 501 的下单请求应被拒绝并返回明确错误码。"
    ),
}
BAD_REQ = {
    "title": "系统体验优化",
    "source": "paste",
    "body": "希望系统整体更好用，提升用户体验，尽快优化一下。",
}


def wait_generation_done(gcode: str, timeout: int = 180) -> dict:
    dl = time.time() + timeout
    while time.time() < dl:
        g = api("GET", f"/api/generations/{gcode}")
        if g["status"] in ("done", "failed"):
            return g
        time.sleep(1.5)
    return {"status": "timeout", "events": []}


def main() -> int:
    print("== TestForge M3 验收（需求+RAG）==")
    global HEADERS
    HEADERS = {"X-Trace-Id": f"tr_{uuid.uuid4().hex[:12]}"}

    # ① 前置：仓库 + wiki 就绪（知识就绪 G1）
    repo = api("POST", "/api/repos", {"url": REPO_URL, "branch": "main"})
    rid = int(repo["id"])
    api("POST", f"/api/repos/{rid}/pull")

    # ② 可测需求：四步管线 → 待人审
    good = api("POST", "/api/requirements/ingest", {**GOOD_REQ, "repo_id": rid})
    gcode = good["code"]
    rep = good["report"]
    check("需求解析四步管线", len(rep.get("pipeline", [])) == 4, " > ".join(rep.get("pipeline", [])))
    check("规则抽取（用户故事/边界/权限/AC）", len(rep.get("rules", [])) >= 3, f"{len(rep['rules'])} 条")
    check("可测性评分 ≥80 → 待人审", good["testability"] >= 80 and good["status"] == "待人审", f"score={good['testability']:.0f}")
    check("需求状态机落库（解析中→待人审）", good["status"] == "待人审")

    # ③ 冲突需求：Wiki diff 命中 999→500 → 规则冲突待确认
    conflict = api("POST", "/api/requirements/ingest", {**CONFLICT_REQ, "repo_id": rid})
    check("三方一致性：需求 vs 实现冲突被标记", conflict["status"] == "规则冲突待确认" and conflict["report"]["conflict"], conflict["report"].get("conflict_detail", "")[:60])

    # ④ 不可测需求：G0 自动打回
    bad = api("POST", "/api/requirements/ingest", {**BAD_REQ, "repo_id": rid})
    check("可测性 <80 自动打回（G0，无人工）", bad["status"] == "已打回" and bad["testability"] < 80, f"score={bad['testability']:.0f}")

    # ⑤ confirm 生效 → 自动编排 → 执行回填
    before_pages = {p["id"]: p for p in api("GET", f"/api/wiki?repo_id={rid}")}
    confirmed = api("POST", f"/api/requirements/{conflict['id']}/confirm", {"action": "approve"})
    check("冲突确认 → 已生效 + wiki 规则页更新", confirmed["status"] == "已生效")
    time.sleep(2)
    after_pages = {p["id"]: p for p in api("GET", f"/api/wiki?repo_id={rid}")}
    wiki_updated = any(after_pages[i]["rev"] > before_pages[i]["rev"] for i in before_pages if i in after_pages)
    check("wiki 规则页 rev+1（需求确认新增规则）", wiki_updated)

    gen = wait_generation_done(confirmed["generation_id"])
    stages = [e["stage"] for e in gen.get("events", [])]
    check("需求驱动自动编排生成（无需人工）", gen["status"] == "done", " > ".join(dict.fromkeys(stages)))
    cases = api("GET", f"/api/cases?source_req={confirmed['code']}")
    check("生成用例关联需求源头", cases["total"] >= 9, f"{cases['total']} 条 source_req={confirmed['code']}")
    check("用例全部入库且带 traceID", cases["items"] and all(c["status"] == "已入库" and c["trace_id"].startswith("tr_") for c in cases["items"]))

    # ⑥ pgvector 相似用例检索
    sims = api("GET", f"/api/requirements/{gcode}/similar?limit=5")
    check("相似用例 RAG 检索返回 top-k", 0 < len(sims) <= 5, f"top: {sims[0]['code'] if sims else '-'} ({sims[0]['score'] if sims else 0})")

    # ⑦ 质量流水线 G0~G5
    q = api("GET", "/api/quality/requirements")
    row = next((x for x in q if x["code"] == confirmed["code"]), None)
    check("质量流水线六关卡矩阵", row is not None and len(row["gates"]) == 6, f"通过 {row['gates_passed']}/6 质量分 {row['quality_score']}" if row else "")
    check("G0~G3 自动判定通过", row is not None and all(row["gates"][g]["ok"] for g in ("G0 可测性", "G1 知识就绪", "G2 用例覆盖", "G3 执行验证")))
    check("质量分加权计算", row is not None and row["quality_score"] > 0, f"{row['quality_score']}")
    check("人工介入次数记录", row is not None and row["manual_interventions"] >= 1, f"{row['manual_interventions']} 次")

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    print(f"== 结果: {passed}/{len(CHECKS)} ==")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
