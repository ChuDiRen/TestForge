"""M1 验收：单仓闭环 接仓库→生成→执行→入库（全 mock 环境）。

验收标准（PROMPT §10）：
- 接入 fixtures/sample-repo → tree-sitter 索引 → 选中 create_order → 生成 → 沙箱执行 → 用例入库
- 产出 ≥9 条用例、含 边界/异常/权限 三类、全部执行通过、每条带 traceID
"""

import json
import os
import sys
import time

import httpx

GATEWAY = f"http://127.0.0.1:{os.environ.get('GATEWAY_PORT', '8000')}"
REPO_URL = os.environ.get("TF_SAMPLE_REPO_URL", "file:///mnt/e/TestForge/fixtures/sample-repo")
TARGET = os.environ.get("TF_TARGET", "create_order")

CHECKS: list[tuple[str, bool, str]] = []
HEADERS: dict = {}


def check(name: str, cond: bool, detail: str = "") -> None:
    CHECKS.append((name, cond, detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def api(method: str, path: str, body: dict | None = None, timeout: float = 60) -> dict:
    r = httpx.request(method, f"{GATEWAY}{path}", json=body, timeout=timeout, headers=HEADERS)
    data = r.json()
    assert data.get("code") == 0, f"{path} -> {data}"
    return data["data"]


def main() -> int:
    print("== TestForge M1 验收（单仓闭环，全 mock）==")
    global HEADERS
    import uuid

    trace_all = f"tr_{uuid.uuid4().hex[:12]}"  # 全程贯穿 trace
    HEADERS = {"X-Trace-Id": trace_all}

    # ① 接入仓库（repo-svc: clone → tree-sitter 索引 → 调用图）
    repo = api("POST", "/api/repos", {"url": REPO_URL, "branch": "main"})
    rid = repo["id"]
    check("仓库接入", repo["status"] == "已接入", f"id={rid}")

    # ② 拉取（增量索引流水线）
    pull = api("POST", f"/api/repos/{rid}/pull")
    check("拉取+索引流水线", pull["functions"] > 0 and pull["call_edges"] > 0, f"函数={pull['functions']} 调用边={pull['call_edges']}")

    # ③ tree-sitter 索引确认 create_order
    fns = api("GET", f"/api/functions?repo_id={rid}&name={TARGET}")
    co = next((f for f in fns if f["name"] == TARGET), None)
    check(f"tree-sitter 索引到 {TARGET}", co is not None, co["signature"] if co else "")

    # ④ 生成（两阶段+守卫，SSE 事件链；沿用贯穿 trace）
    gen = api("POST", "/api/generations", {"function": TARGET, "repo_id": rid, "layer": "ut"})
    gcode, trace_id = gen["generation_id"], gen["trace_id"]
    check("生成任务创建", gcode.startswith("GEN-") and trace_id == trace_all, f"trace={trace_id}")

    deadline = time.time() + 180
    final = None
    while time.time() < deadline:
        g = api("GET", f"/api/generations/{gcode}")
        if g["status"] in ("done", "failed"):
            final = g
            break
        time.sleep(1.5)
    stages = [e["stage"] for e in (final or {}).get("events", [])]
    check("生成管线完成 (plan→guard→codegen→sandbox→coverage)", final is not None and final["status"] == "done", " > ".join(dict.fromkeys(stages)))
    if final is None or final["status"] != "done":
        return 1

    # ⑤ 用例入库检查（口径：本次生成 gen_id）
    cases = api("GET", "/api/cases")
    mine = [x for x in cases["items"] if x.get("gen_id") == gcode]
    cats = {x["category"] for x in mine}
    check("产出 ≥9 条用例", len(mine) >= 9, f"实际 {len(mine)}")
    check("含 边界/异常/权限 三类", {"boundary", "exception", "permission"} <= cats, f"类别 {sorted(cats)}")
    check("全部执行通过（已入库）", mine and all(x["status"] == "已入库" for x in mine), f"已入库 {sum(1 for x in mine if x['status'] == '已入库')}/{len(mine)}")
    check("每条带 traceID", all(x["trace_id"].startswith("tr_") for x in mine))

    # ⑥ 执行记录
    runs = api("GET", "/api/runs")
    run = next((r for r in runs if r["gen_id"] == gcode), None)
    check("执行记录生成", run is not None and run["status"] == "success",
          f"{run['pass_count']}/{run['pass_total']} 覆盖率={run['coverage']}% 修复={run['repair_rounds']}轮 sandbox={run['sandbox_status']}" if run else "")

    # ⑦ traceID 全链路回溯（仓库→生成→执行 同一 trace 贯穿）
    tr = api("GET", f"/api/traces/{trace_all}")
    types = [e["type"] for e in tr.get("events", [])]
    check("traceID 链路可回溯（仓库→生成→执行）", {"仓库", "生成", "执行"} <= set(types), f"{len(tr.get('events', []))} 事件: {types}")

    # ⑧ 真实 pytest 复核（fake 沙箱之外的实证：本机直跑生成代码）
    real = _real_pytest_evidence(gcode, trace_id)
    check("生成代码真实 pytest 复核通过", real, "")

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    print(f"== 结果: {passed}/{len(CHECKS)} ==")
    return 0 if passed == len(CHECKS) else 1


def _real_pytest_evidence(gen_code: str, trace_id: str) -> bool:
    """从 generation 记录取 code_file，落到临时目录真实执行。"""
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    g = httpx.get(f"{GATEWAY}/api/generations/{gen_code}", timeout=30).json()["data"]
    code = ""
    for e in g["events"]:
        try:
            payload = json.loads(e.get("payload_json") or "{}")
        except json.JSONDecodeError:
            continue
        if payload.get("code"):
            code = payload["code"]
    if not code:
        return False
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        shutil.copytree(root / "fixtures" / "sample-repo" / "app", ws / "app")
        tests = ws / "tests"
        tests.mkdir()
        (tests / "test_gen.py").write_text(code, encoding="utf-8")
        (ws / "conftest.py").write_text("import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).parent))\n", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--disable-warnings", "tests/test_gen.py"],
            cwd=ws, capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace",
        )
        tail = (proc.stdout or "").strip().splitlines()[-1:] or [""]
        print(f"  [real-pytest] exit={proc.returncode} {tail[0][:100]}")
        return proc.returncode == 0


if __name__ == "__main__":
    sys.exit(main())
