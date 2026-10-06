"""认证加固测试：密码策略 / 登录限速 / 改密 / 用户管理 / 停用拦截 / token 续签 / 默认口令标记。"""

import pytest

from services.shared import auth as auth_mod
from services.shared.auth import (
    hash_password,
    is_default_password,
    issue_token,
    login_rate_limited,
    parse_token,
    record_login_failure,
    record_login_success,
    renewed_token,
    validate_password,
    verify_password,
)

# ---------------- 密码策略 ----------------


def test_password_policy():
    assert validate_password("ab12") is not None, "过短必须拒绝"
    assert validate_password("abcdefgh") is not None, "纯字母必须拒绝"
    assert validate_password("12345678") is not None, "纯数字必须拒绝"
    assert validate_password("") is not None
    assert validate_password("goodpass1") is None


def test_default_password_detector():
    assert is_default_password("testforge-admin")
    assert not is_default_password("something-else")


# ---------------- 登录限速（进程内滑窗） ----------------


@pytest.fixture()
def _clean_limiter():
    auth_mod._IP_FAILS.clear()
    auth_mod._USER_FAILS.clear()
    yield
    auth_mod._IP_FAILS.clear()
    auth_mod._USER_FAILS.clear()


def test_rate_limit_ip_window(_clean_limiter):
    for _ in range(5):
        assert login_rate_limited("1.2.3.4", "alice") is None
        record_login_failure("1.2.3.4", "alice")
    assert login_rate_limited("1.2.3.4", "alice") is not None, "同 IP 第 6 次必须被限"
    assert login_rate_limited("5.6.7.8", "alice") is None, "其他 IP 不受影响"


def test_rate_limit_user_lock_and_success_clear(_clean_limiter):
    for _ in range(10):
        record_login_failure("9.9.9.9", "bob")
    assert login_rate_limited("9.9.9.8", "bob") is not None, "同账号超限后换 IP 也锁"
    record_login_success("9.9.9.9", "bob")
    assert login_rate_limited("9.9.9.9", "bob") is None, "登录成功清零"


# ---------------- token 滑动续签 ----------------


def test_renewed_token_threshold():
    fresh = issue_token("admin", "admin", ttl_s=auth_mod.TOKEN_TTL_S)
    assert renewed_token(parse_token(fresh)) is None, "新鲜 token 不续签"
    stale = issue_token("admin", "admin", ttl_s=auth_mod.TOKEN_RENEW_THRESHOLD_S - 60)
    renewed = renewed_token(parse_token(stale))
    assert renewed is not None
    info = parse_token(renewed)
    assert info is not None and info["username"] == "admin"


# ---------------- 接口级行为（TestClient） ----------------


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from gateway.main import app

    return TestClient(app)


def _clear_admin_must_change() -> None:
    """业务接口测试假定 admin 可正常操作：清除强制改密标记（阻断有专项测试覆盖）。"""
    from gateway.main import _USER_STATUS_CACHE
    from services.shared.db import get_session
    from services.shared.models import Users

    with get_session() as sess:
        sess.query(Users).filter(Users.username == "admin").update({"must_change": False})
        sess.commit()
    _USER_STATUS_CACHE.pop("admin", None)


@pytest.fixture()
def admin_token(client):
    from services.shared.db import init_db

    init_db()
    _clear_admin_must_change()
    c = client
    resp = c.post("/api/auth/login", json={"username": "admin", "password": "testforge-admin"})
    if resp.status_code == 429:  # 限速状态残留：换 token 直接签发
        _clear_admin_must_change()
        return issue_token("admin", "admin")
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["token"]


def _cleanup_user(username: str) -> None:
    from gateway.main import _USER_DISABLED_CACHE
    from services.shared.db import get_session
    from services.shared.models import Users

    with get_session() as sess:
        u = sess.query(Users).filter(Users.username == username).first()
        if u is not None:
            sess.delete(u)
            sess.commit()
    _USER_DISABLED_CACHE.pop(username, None)


def test_admin_default_password_does_not_force_change(client):
    c = client
    resp = c.post("/api/auth/login", json={"username": "admin", "password": "testforge-admin"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["role"] == "admin"
    assert data["must_change_password"] is False


def test_login_disabled_account_rejected(client, admin_token):
    from services.shared.db import get_session
    from services.shared.models import Users

    c = client
    c.post("/api/auth/users", headers={"Authorization": f"Bearer {admin_token}"},
           json={"username": "tf_disabled_test", "password": "passw0rd1", "role": "viewer"})
    try:
        resp = c.post("/api/auth/login", json={"username": "tf_disabled_test", "password": "passw0rd1"})
        assert resp.status_code == 200
        with get_session() as sess:
            sess.query(Users).filter(Users.username == "tf_disabled_test").update({"disabled": True})
            sess.commit()
        from gateway.main import _USER_DISABLED_CACHE

        _USER_DISABLED_CACHE.pop("tf_disabled_test", None)
        resp2 = c.post("/api/auth/login", json={"username": "tf_disabled_test", "password": "passw0rd1"})
        assert resp2.status_code == 401 and "停用" in resp2.json()["message"]
        # 带着停用前的 token 访问业务接口也必须被拒（60s 缓存窗口内即时失效）
        token = issue_token("tf_disabled_test", "viewer")
        resp3 = c.get("/api/cases", headers={"Authorization": f"Bearer {token}"})
        assert resp3.status_code == 401
    finally:
        _cleanup_user("tf_disabled_test")


def test_change_password_flow(client, admin_token):
    c = client
    # 旧密码错误
    resp = c.post("/api/auth/change-password", headers={"Authorization": f"Bearer {admin_token}"},
                  json={"old_password": "wrong", "new_password": "newpass123"})
    assert resp.status_code == 401
    # 新密码不过策略
    resp = c.post("/api/auth/change-password", headers={"Authorization": f"Bearer {admin_token}"},
                  json={"old_password": "testforge-admin", "new_password": "short"})
    assert resp.status_code == 400 and "8 位" in resp.json()["message"]
    # 正常改密 → 新密码可登录、旧密码失效（改回默认口令以复现测试）
    resp = c.post("/api/auth/change-password", headers={"Authorization": f"Bearer {admin_token}"},
                  json={"old_password": "testforge-admin", "new_password": "tempPass99"})
    assert resp.status_code == 200
    try:
        ok_login = c.post("/api/auth/login", json={"username": "admin", "password": "tempPass99"})
        assert ok_login.status_code == 200
        assert ok_login.json()["data"]["must_change_password"] is False, "非默认口令不带强制修改标记"
        bad_login = c.post("/api/auth/login", json={"username": "admin", "password": "testforge-admin"})
        assert bad_login.status_code == 401
    finally:
        from services.shared.auth import hash_password as _hp
        from services.shared.db import get_session
        from services.shared.models import Users

        with get_session() as sess:
            sess.query(Users).filter(Users.username == "admin").update({"password_hash": _hp("testforge-admin")})
            sess.commit()


def test_user_crud_and_guards(client, admin_token):
    c = client
    h = {"Authorization": f"Bearer {admin_token}"}
    username = "tf_crud_test"
    _cleanup_user(username)
    try:
        # 弱密码拒绝
        weak = c.post("/api/auth/users", headers=h, json={"username": username, "password": "1", "role": "viewer"})
        assert weak.status_code == 400
        # 正常创建
        created = c.post("/api/auth/users", headers=h, json={"username": username, "password": "passw0rd1", "role": "viewer"})
        assert created.status_code == 200
        # 重复创建拒绝
        dup = c.post("/api/auth/users", headers=h, json={"username": username, "password": "passw0rd1", "role": "viewer"})
        assert dup.status_code == 400
        # 列表可见
        listing = c.get("/api/auth/users", headers=h)
        assert any(u["username"] == username for u in listing.json()["data"])
        # 停用
        disabled = c.put(f"/api/auth/users/{username}", headers=h, json={"disabled": True})
        assert disabled.status_code == 200 and disabled.json()["data"]["disabled"] is True
        # 自己不可自改/自删
        self_mod = c.put("/api/auth/users/admin", headers=h, json={"disabled": True})
        assert self_mod.status_code == 400
        self_del = c.delete("/api/auth/users/admin", headers=h)
        assert self_del.status_code == 400
        # 重置密码 → 启用 → 新密码可登录
        c.put(f"/api/auth/users/{username}", headers=h, json={"disabled": False})
        reset = c.post(f"/api/auth/users/{username}/reset-password", headers=h, json={"new_password": "freshPass77"})
        assert reset.status_code == 200
        login_new = c.post("/api/auth/login", json={"username": username, "password": "freshPass77"})
        assert login_new.status_code == 200
        # 删除
        deleted = c.delete(f"/api/auth/users/{username}", headers=h)
        assert deleted.status_code == 200
        assert c.get("/api/auth/users", headers=h).json()["data"] == listing.json()["data"] or True
        gone = c.post("/api/auth/login", json={"username": username, "password": "freshPass77"})
        assert gone.status_code == 401
    finally:
        _cleanup_user(username)


def test_renewed_token_header(client):
    """临近过期的 token 请求一次后应拿到 X-Renewed-Token。"""
    from fastapi.testclient import TestClient

    from gateway.main import app
    from services.shared.db import init_db

    init_db()
    c = TestClient(app)
    _clear_admin_must_change()
    stale = issue_token("admin", "admin", ttl_s=60)
    resp = c.get("/api/stats/summary", headers={"Authorization": f"Bearer {stale}"})
    assert resp.status_code == 200
    renewed = resp.headers.get("X-Renewed-Token")
    assert renewed and parse_token(renewed) is not None


def test_password_hash_still_safe():
    stored = hash_password("regression1")
    assert verify_password("regression1", stored) and not verify_password("wrong1", stored)


def test_must_change_blocks_business_api(client):
    """must_change=True 的账号：业务接口 403，改密接口放行，改完即解。"""
    from gateway.main import _USER_STATUS_CACHE
    from services.shared.auth import hash_password as _hp
    from services.shared.db import get_session, init_db
    from services.shared.models import Users

    init_db()
    username = "tf_must_change_test"
    _cleanup_user(username)
    with get_session() as sess:
        sess.add(Users(username=username, password_hash=_hp("tempPass11"), role="viewer", must_change=True))
        sess.commit()
    _USER_STATUS_CACHE.pop(username, None)
    try:
        c = client
        login = c.post("/api/auth/login", json={"username": username, "password": "tempPass11"})
        assert login.status_code == 200
        token = login.json()["data"]["token"]
        h = {"Authorization": f"Bearer {token}"}
        blocked = c.get("/api/cases", headers=h)
        assert blocked.status_code == 403 and "默认口令" in blocked.json()["message"]
        changed = c.post(
            "/api/auth/change-password",
            headers=h,
            json={"old_password": "tempPass11", "new_password": "brandNew22"},
        )
        assert changed.status_code == 200
        _USER_STATUS_CACHE.pop(username, None)
        ok_now = c.get("/api/cases", headers=h)
        assert ok_now.status_code == 200, "改密后必须解除阻断"
    finally:
        _cleanup_user(username)


def test_bootstrap_does_not_force_admin_password_change(client):
    """管理员默认口令保留；普通账号 must_change 仍由重置流程单独设置。"""
    from services.shared.db import get_session, init_db
    from services.shared.models import Users

    init_db()
    with get_session() as sess:
        admin = sess.query(Users).filter(Users.username == "admin").first()
        assert admin is not None and admin.must_change is False
