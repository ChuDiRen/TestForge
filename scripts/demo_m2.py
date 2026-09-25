"""M2 验收：Wiki 层 —— 分层摘要 + git diff 增量重建 + stale 标记。

验收（PROMPT §10）：改 sample-repo 一行 → 相关页 stale → 重建后仅受影响页 rev+1。
"""

import os
import pathlib
import subprocess
import sys
import time
from pathlib import Path

import httpx

GATEWAY = f"http://127.0.0.1:{os.environ.get('GATEWAY_PORT', '8000')}"
_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_URL = os.environ.get(
    "TF_SAMPLE_REPO_URL",
    (_ROOT / "fixtures" / "sample-repo").as_uri() if os.name == "nt" else "file:///mnt/e/TestForge/fixtures/sample-repo",
)
# Windows 侧运行 demo 时，sample-repo 的源目录（fixtures）路径
SRC = Path(os.environ.get("TF_SAMPLE_REPO_SRC", "fixtures/sample-repo")).resolve()

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


def git(*args: str) -> None:
    subprocess.run(["git", "-C", str(SRC), *args], check=True, capture_output=True)


def rev() -> str:
    return subprocess.run(["git", "-C", str(SRC), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def wiki_snapshot(repo_id: int) -> dict[int, dict]:
    pages = api("GET", f"/api/wiki?repo_id={repo_id}")
    return {p["id"]: p for p in pages}


def main() -> int:
    print("== TestForge M2 验收（Wiki 层）==")
    import uuid

    global HEADERS
    HEADERS = {"X-Trace-Id": f"tr_{uuid.uuid4().hex[:12]}"}

    # ① 接入（或复用）sample-repo 并全量编译 Wiki
    repo = api("POST", "/api/repos", {"url": REPO_URL, "branch": "main"})
    rid = int(repo["id"])  # protobuf int64 经 JSON 为字符串
    api("POST", f"/api/repos/{rid}/pull")
    pages = api("GET", f"/api/wiki?repo_id={rid}")
    levels = {p["level"] for p in pages}
    fn_pages = [p for p in pages if p["level"] == "function"]
    check("分层编译产出 repo/module/function 三层页面", {"repo", "module", "function"} <= levels, f"共 {len(pages)} 页，函数卡片 {len(fn_pages)}")
    check("函数卡片含 ground truth 源码", fn_pages and all(p["rev"] >= 1 for p in fn_pages))

    detail = api("GET", f"/api/wiki/{fn_pages[0]['id']}")
    check("卡片内容含源码与调用关系", "```python" in detail["content_md"] and "调用方" in detail["content_md"], detail["title"])

    # ② 改 sample-repo 一行（含调用方传播演示：改 inventory/client.py，orders 模块是其调用方）
    before = wiki_snapshot(rid)
    target_file = SRC / "app" / "inventory" / "client.py"
    original = target_file.read_text(encoding="utf-8")
    marker = f"# M2-DEMO touch {int(time.time())}\n"
    target_file.write_text(marker + original, encoding="utf-8")
    touched = True
    affected_scope = {"get_stock", "get_price", "reserve", "release", "create_order"}
    try:
        git("add", "-A")
        git("-c", "user.name=TestForge", "-c", "user.email=tf@local", "commit", "-m", "m2 demo: touch inventory client")
        api("POST", f"/api/repos/{rid}/pull")
        after_pull = wiki_snapshot(rid)
        stale_pages = [p for p in after_pull.values() if p["stale"]]
        check("变更后相关页自动置 stale（调用方跨模块传播）", len(stale_pages) > 0,
              f"stale: {sorted({p['function'] or p['module'] or p['level'] for p in stale_pages})}")
        rev_bumped = [p for p in after_pull.values() if before.get(p["id"]) and p["rev"] > before[p["id"]]["rev"]]
        in_scope = all(
            p["level"] in ("repo",)
            or (p["function"] in affected_scope)
            or (p["module"] in {"app.inventory.client", "app.orders.service"})
            for p in rev_bumped
        )
        check("rev+1 仅发生在受影响范围（变更文件+调用方模块+总览）", in_scope and len(rev_bumped) > 0,
              f"rev+1 页: {sorted({p['function'] or p['module'] or p['level'] for p in rev_bumped})}")
        violators = [
            f"{after_pull[i]['function']} rev{before[i]['rev']}->{after_pull[i]['rev']} stale={after_pull[i]['stale']}"
            for i in before
            if after_pull[i]["level"] == "function"
            and after_pull[i]["function"] not in affected_scope
            and (after_pull[i]["rev"] != before[i]["rev"] or after_pull[i]["stale"])
        ]
        check("无关函数卡片不受影响", not violators, "; ".join(violators))
    finally:
        # 还原源仓库一行（保持 fixture 干净）
        if touched:
            target_file.write_text(original, encoding="utf-8")
            git("add", "-A")
            git("-c", "user.name=TestForge", "-c", "user.email=tf@local", "commit", "-m", "m2 demo: revert touch")

    # ③ 增量重建（revert 也是一次 diff：受影响页再次 rev+1）
    api("POST", f"/api/repos/{rid}/pull")
    after = wiki_snapshot(rid)
    rebuilt = [after[i] for i in after if i in before and after[i]["rev"] > before[i]["rev"]]
    check("重建后受影响页 rev+1", len(rebuilt) > 0, f"rev 提升 {len(rebuilt)} 页 / 总 {len(after)} 页")

    # ④ Wiki 健康度 + 一键重建（全量重建后 stale 全清）
    rb = api("POST", "/api/wiki/rebuild", {"repo_id": rid, "full": True})
    check("一键全量重建可用", rb["pages_rebuilt"] > 0, f"重建 {rb['pages_rebuilt']} 页")
    after_full = wiki_snapshot(rid)
    stale_left = [f"{p['function'] or p['module']}(repo{p['repo_id']})" for p in after_full.values() if p["stale"]]
    check("全量重建后 stale 全部清除", not stale_left, f"残留: {stale_left[:4]} rid={rid}")
    health = api("GET", "/api/wiki/health")
    row = next((h for h in health if h["repo_id"] == rid), None)
    check("Wiki 健康度面板数据", row is not None and row["pages"] == len(after_full) and row["stale"] == 0,
          f"pages={row['pages'] if row else '-'} stale={row['stale'] if row else '-'}")

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    print(f"== 结果: {passed}/{len(CHECKS)} ==")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
