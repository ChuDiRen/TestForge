"""健康检查：后端 /api/system/services 必须全绿。"""

import sys

import httpx
from _auth import auth_headers


def main() -> int:
    port = __import__("os").environ.get("GATEWAY_PORT", "8000")
    base = f"http://127.0.0.1:{port}"
    try:
        r = httpx.get(f"{base}/api/system/services", timeout=30, headers=auth_headers())
        r.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        print(f"[healthcheck] 后端不可达: {exc}")
        return 1
    data = r.json()["data"]
    for s in data["services"]:
        mark = "OK " if s["ok"] and s["db_ok"] else "FAIL"
        print(f"  [{mark}] {s['name']:<20} :{s['port']}")
    if data["all_green"]:
        print("[healthcheck] 全部服务 GREEN")
        return 0
    print("[healthcheck] 存在未就绪服务")
    return 1


if __name__ == "__main__":
    sys.exit(main())
