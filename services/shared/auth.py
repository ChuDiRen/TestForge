"""认证：PBKDF2 口令哈希 + HMAC 签名 token + 首启引导管理员。

token 形如 ``username:expiry_ts:role:hmac_sig``，网关中间件本地校验（无状态、无会话表）。
角色：admin 全权；viewer 仅 GET。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from services.shared.config import get_settings

TOKEN_TTL_S = 12 * 3600
_PBKDF2_ITER = 100_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _PBKDF2_ITER)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    digest_check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _PBKDF2_ITER)
    return hmac.compare_digest(digest_check.hex(), digest)


def _sign(payload: str) -> str:
    return hmac.new(get_settings().secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()


def issue_token(username: str, role: str, ttl_s: int = TOKEN_TTL_S) -> str:
    payload = f"{username}:{int(time.time()) + ttl_s}:{role}"
    return f"{payload}:{_sign(payload)}"


def parse_token(token: str) -> dict[str, str] | None:
    """校验签名与有效期；合法返回 {username, role}，否则 None。"""
    parts = token.split(":")
    if len(parts) != 4:
        return None
    username, exp, role, sig = parts
    if not hmac.compare_digest(_sign(f"{username}:{exp}:{role}"), sig):
        return None
    if not exp.isdigit() or int(exp) < time.time():
        return None
    if role not in ("admin", "viewer"):
        return None
    return {"username": username, "role": role}


def bootstrap_admin() -> None:
    """users 表为空时创建 admin 账号（密码取 settings.admin_password）。"""
    from services.shared.db import get_session
    from services.shared.models import Users

    with get_session() as sess:
        if sess.query(Users).count() > 0:
            return
        s = get_settings()
        sess.add(Users(username="admin", password_hash=hash_password(s.admin_password), role="admin"))
        sess.commit()
