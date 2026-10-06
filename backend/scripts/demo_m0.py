"""M0 验收：骨架全绿 = gateway 可达 + 7 个 gRPC 服务全部 Ping 通 + DB OK + 前端可达。"""

import os
import sys

import httpx
from _auth import auth_headers

GATEWAY = f"http://127.0.0.1:{os.environ.get('GATEWAY_PORT', '8000')}"
FRONTEND = f"http://127.0.0.1:{os.environ.get('FRONTEND_PORT', '5173')}"

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    CHECKS.append((name, cond, detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def main() -> int:
    print("== TestForge M0 验收（骨架全绿）==")

    r = httpx.get(f"{GATEWAY}/api/health", timeout=15)
    check("gateway /api/health", r.status_code == 200 and r.json()["data"]["status"] == "ok")

    svc = httpx.get(f"{GATEWAY}/api/system/services", timeout=30, headers=auth_headers()).json()["data"]
    for s in svc["services"]:
        check(f"gRPC {s['name']}:{s['port']} Ping", s["ok"] and s["db_ok"], s.get("error", ""))
    check("gateway→gRPC 链路全绿（7 服务）", svc["all_green"])

    stats = httpx.get(f"{GATEWAY}/api/stats/summary", timeout=15, headers=auth_headers()).json()["data"]
    check("统计接口返回结构化数据", "cases_total" in stats)

    try:
        fr = httpx.get(FRONTEND, timeout=10)
        check("前端可访问", fr.status_code == 200 and "TestForge" in fr.text)
    except Exception as exc:  # noqa: BLE001
        check("前端可访问", False, str(exc)[:80])

    total = len(CHECKS)
    passed = sum(1 for _, ok, _ in CHECKS if ok)
    print(f"== 结果: {passed}/{total} ==")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
