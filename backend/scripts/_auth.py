"""脚本统一认证：登录 admin 拿 token（demo/seed 脚本调用带认证的 REST API 用）。

密码来源 TF_ADMIN_PASSWORD 环境变量，缺省 testforge-admin（与 settings 默认一致）。
"""

from __future__ import annotations

import os

import httpx

_TOKEN: str = ""


def auth_headers() -> dict:
    """返回带 Bearer token 的请求头（进程内缓存，首次调用登录）。"""
    global _TOKEN
    if not _TOKEN:
        gw = f"http://127.0.0.1:{os.environ.get('APP_PORT', '8000')}"
        pw = os.environ.get("TF_ADMIN_PASSWORD", "testforge-admin")
        r = httpx.post(f"{gw}/api/auth/login", json={"username": "admin", "password": pw}, timeout=15)
        assert r.status_code == 200, f"脚本登录失败: {r.status_code} {r.text[:200]}"
        _TOKEN = r.json()["data"]["token"]
    return {"Authorization": f"Bearer {_TOKEN}"}
