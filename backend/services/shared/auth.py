"""认证：PBKDF2 口令哈希 + HMAC 签名 token + 登录限速 + 首启引导管理员。

token 形如 ``username:expiry_ts:role:hmac_sig``，网关中间件本地校验（无状态、无会话表）。
角色：admin 全权；viewer 仅 GET。
限速：进程内滑动窗口——同 IP 60s 内 5 次失败、同账号 15min 内 10 次失败即拒绝（登录成功清零）。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import defaultdict

from services.shared.config import get_settings

TOKEN_TTL_S = 12 * 3600
TOKEN_RENEW_THRESHOLD_S = 6 * 3600  # 剩余寿命低于该值时中间件自动续签（X-Renewed-Token）
_PBKDF2_ITER = 100_000

# 登录限速（进程内滑窗；mono 单进程即全局）
_IP_FAILS: dict[str, list[float]] = defaultdict(list)
_USER_FAILS: dict[str, list[float]] = defaultdict(list)
_IP_WINDOW_S, _IP_MAX = 60.0, 5
_USER_WINDOW_S, _USER_MAX = 900.0, 10
_USER_LOCK_S = 300.0


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


def validate_password(password: str) -> str | None:
    """密码策略：≥8 位且同时含字母与数字。合法返回 None，否则返回错误文案。"""
    pw = password or ""
    if len(pw) < 8:
        return "密码至少 8 位"
    if not any(c.isalpha() for c in pw) or not any(c.isdigit() for c in pw):
        return "密码必须同时包含字母和数字"
    return None


def login_rate_limited(ip: str, username: str) -> str | None:
    """登录前检查限速；被限返回文案，否则 None。失败由 record_login_failure 登记。"""
    now = time.time()
    ip_fails = [t for t in _IP_FAILS.get(ip, ()) if now - t < _IP_WINDOW_S]
    _IP_FAILS[ip] = ip_fails
    if len(ip_fails) >= _IP_MAX:
        return "尝试过于频繁，请 1 分钟后再试"
    user_fails = [t for t in _USER_FAILS.get(username, ()) if now - t < _USER_WINDOW_S]
    _USER_FAILS[username] = user_fails
    if len(user_fails) >= _USER_MAX:
        locked_for = int(_USER_LOCK_S - (now - user_fails[-1]))
        return f"该账号失败次数过多，已临时锁定约 {max(locked_for, 5) // 60 + 1} 分钟"
    return None


def record_login_failure(ip: str, username: str) -> None:
    now = time.time()
    _IP_FAILS[ip].append(now)
    _USER_FAILS[username].append(now)


def record_login_success(ip: str, username: str) -> None:
    _IP_FAILS.pop(ip, None)
    _USER_FAILS.pop(username, None)


def is_default_password(password: str) -> bool:
    """是否仍是首启默认口令（用于触发强制修改）。"""
    return (password or "") == get_settings().admin_password and get_settings().admin_password == "testforge-admin"


def _sign(payload: str) -> str:
    return hmac.new(get_settings().secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()


def issue_token(username: str, role: str, ttl_s: int = TOKEN_TTL_S) -> str:
    payload = f"{username}:{int(time.time()) + ttl_s}:{role}"
    return f"{payload}:{_sign(payload)}"


def parse_token(token: str) -> dict[str, str] | None:
    """校验签名与有效期；合法返回 {username, role, exp}，否则 None。"""
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
    return {"username": username, "role": role, "exp": exp}


def renewed_token(info: dict[str, str]) -> str | None:
    """剩余寿命不足阈值时签发新 token（滑动续期）；无需续签返回 None。"""
    try:
        remaining = int(info["exp"]) - int(time.time())
    except (KeyError, ValueError):
        return None
    if remaining >= TOKEN_RENEW_THRESHOLD_S:
        return None
    return issue_token(info["username"], info["role"])


def bootstrap_admin() -> None:
    """users 表为空时创建 admin 账号；存量账号仍在用首启默认口令的回填 must_change 标记。"""
    from services.shared.db import get_session
    from services.shared.models import Users

    with get_session() as sess:
        # 管理员允许继续使用本地默认口令；不对 admin 回填强制改密标记。
        sess.query(Users).filter(Users.role == "admin", Users.must_change.is_(True)).update(
            {Users.must_change: False}, synchronize_session=False
        )
        sess.commit()
        if sess.query(Users).count() > 0:
            return
        s = get_settings()
        sess.add(
            Users(
                username="admin",
                password_hash=hash_password(s.admin_password),
                role="admin",
                must_change=False,
            )
        )
        sess.commit()
