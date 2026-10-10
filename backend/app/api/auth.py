"""认证路由：登录（限速/审计/停用拦截/默认口令标记）+ 自助改密 + 用户管理。

从 main.py 拆出（与 contracts/graph/knowledge 同模式，尾部导入注册避免循环依赖）。
防呆规则：不能对自己改角色/停用/删除；系统至少保留一个可用 admin。
"""

from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.auth import (
    hash_password,
    issue_token,
    login_rate_limited,
    record_login_failure,
    record_login_success,
    validate_password,
    verify_password,
)
from app.crud import crud_users
from app.main import (
    _USER_STATUS_CACHE,
    ApiError,
    app,
    ok,
)
from app.models import Users
from app.schemas.auth import (
    AuthChangePasswordIn,
    AuthLoginIn,
    AuthResetPasswordIn,
    AuthUserCreateIn,
    AuthUserUpdateIn,
)


def _client_ip(request: Request) -> str:
    if request.headers.get("x-forwarded-for"):
        return request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _last_active_admin_guard(sess, target: str, *, demote: bool = False, disable: bool = False) -> None:
    """防呆：不允许把最后一个可用 admin 降级/停用/删除。"""
    row = sess.query(Users).filter(Users.username == target).first()
    if row is None or row.role != "admin":
        return
    active_admins = (
        sess.query(Users)
        .filter(Users.role == "admin", Users.disabled.is_(False), Users.username != target)
        .count()
    )
    if active_admins == 0:
        raise ApiError(1005, "系统至少保留一个可用管理员账号", 400)


def _require_admin(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user or user.get("role") != "admin":
        raise ApiError(403, "仅管理员可执行该操作", 403)
    return user


@app.post("/api/auth/login")
async def auth_login(request: Request, data: AuthLoginIn, db: Session = Depends(get_db)):
    """登录：限速（同 IP 60s×5 / 同账号 15min×10 锁 5min）→ 停用拦截 → 口令校验 → 审计。"""
    from app.core.trace import emit

    body = data.model_dump()
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    ip = _client_ip(request)
    if not username or not password:
        raise ApiError(1001, "username 与 password 必填")
    limited = login_rate_limited(ip, username)
    if limited:
        emit("认证", username, f"登录被限速 ip={ip}: {limited}")
        raise ApiError(429, limited, 429)
    u = db.query(Users).filter(Users.username == username).first()
    if u is not None and u.disabled:
        emit("认证", username, f"停用账号尝试登录 ip={ip}")
        raise ApiError(401, "账号已被停用，请联系管理员", 401)
    if u is None or not verify_password(password, u.password_hash):
        record_login_failure(ip, username)
        emit("认证", username, f"登录失败 ip={ip}")
        raise ApiError(401, "用户名或密码错误", 401)
    record_login_success(ip, username)
    must_change = bool(u.must_change) and u.role != "admin"
    emit("认证", username, f"登录成功 ip={ip}" + ("（默认口令，待强制修改）" if must_change else ""))
    return ok(
        {
            "token": issue_token(u.username, u.role),
            "username": u.username,
            "role": u.role,
            "must_change_password": must_change,
        }
    )


@app.get("/api/auth/me")
def auth_me(request: Request):
    return ok(getattr(request.state, "user", {"username": "anonymous", "role": "viewer"}))


@app.post("/api/auth/change-password")
async def auth_change_password(request: Request, data: AuthChangePasswordIn, db: Session = Depends(get_db)):
    """自助修改密码：验证旧口令 → 新口令过策略 → 落库 + 审计。"""
    from app.core.trace import emit

    user = getattr(request.state, "user", None)
    if not user:
        raise ApiError(401, "未登录", 401)
    body = data.model_dump()
    old_pw, new_pw = body.get("old_password") or "", body.get("new_password") or ""
    if not old_pw or not new_pw:
        raise ApiError(1001, "old_password 与 new_password 必填")
    policy_error = validate_password(new_pw)
    if policy_error:
        raise ApiError(1004, policy_error, 400)
    u = db.query(Users).filter(Users.username == user["username"]).first()
    if u is None or not verify_password(old_pw, u.password_hash):
        emit("认证", user["username"], "修改密码失败：旧口令不正确")
        raise ApiError(401, "旧密码不正确", 401)
    crud_users.update(db, db_obj=u, obj_in={"password_hash": hash_password(new_pw), "must_change": False})
    from app.main import _USER_STATUS_CACHE

    _USER_STATUS_CACHE.pop(u.username, None)
    emit("认证", u.username, "密码修改成功（自助）")
    return ok({"username": u.username, "changed": True})


@app.get("/api/auth/users")
def auth_list_users(request: Request, db: Session = Depends(get_db)):
    """管理员：用户清单。"""
    _require_admin(request)
    rows = db.query(Users).order_by(Users.id).all()
    return ok(
        [
            {
                "username": r.username,
                "role": r.role,
                "disabled": bool(r.disabled),
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    )


@app.post("/api/auth/users/create")
async def auth_create_user(request: Request, data: AuthUserCreateIn, db: Session = Depends(get_db)):
    """管理员创建账号（role: admin|viewer；密码过策略）。"""
    from app.core.trace import emit

    user = _require_admin(request)
    body = data.model_dump()
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    role = body.get("role") or "viewer"
    if not username or not password:
        raise ApiError(1001, "username 与 password 必填")
    if role not in ("admin", "viewer"):
        raise ApiError(1001, "role 仅支持 admin|viewer")
    policy_error = validate_password(password)
    if policy_error:
        raise ApiError(1004, policy_error, 400)
    if db.query(Users).filter(Users.username == username).first() is not None:
        raise ApiError(1002, "用户名已存在", 400)
    crud_users.create(db, obj_in={"username": username, "password_hash": hash_password(password), "role": role})
    emit("认证", user["username"], f"创建账号 {username}（{role}）")
    return ok({"username": username, "role": role})


@app.put("/api/auth/users/{username}")
async def auth_update_user(username: str, request: Request, data: AuthUserUpdateIn, db: Session = Depends(get_db)):
    """管理员更新账号：改角色 / 停用 / 启用。不可操作自己；保底一个可用 admin。"""
    from app.core.trace import emit

    user = _require_admin(request)
    body = data.model_dump()
    role = body.get("role")
    disabled = body.get("disabled")
    if role is None and disabled is None:
        raise ApiError(1001, "无可更新字段（支持 role/disabled）")
    if role is not None and role not in ("admin", "viewer"):
        raise ApiError(1001, "role 仅支持 admin|viewer")
    if username == user["username"]:
        raise ApiError(1005, "不能修改自己的角色或停用状态", 400)
    u = db.query(Users).filter(Users.username == username).first()
    if u is None:
        raise ApiError(404, "用户不存在", 404)
    _last_active_admin_guard(db, username, demote=role == "viewer", disable=bool(disabled))
    changed = []
    patch: dict = {}
    if role is not None and role != u.role:
        patch["role"] = role
        changed.append(f"角色→{role}")
    if disabled is not None and bool(disabled) != bool(u.disabled):
        patch["disabled"] = bool(disabled)
        changed.append("停用" if disabled else "启用")
    if patch:
        crud_users.update(db, db_obj=u, obj_in=patch)
    _USER_STATUS_CACHE.pop(username, None)
    emit("认证", user["username"], f"更新账号 {username}: {', '.join(changed) or '无变更'}")
    return ok({"username": username, "role": u.role, "disabled": bool(u.disabled)})


@app.post("/api/auth/users/{username}/delete")
async def auth_delete_user(username: str, request: Request, db: Session = Depends(get_db)):
    """管理员删除账号（不可删自己；保底一个可用 admin）。"""
    from app.core.trace import emit

    user = _require_admin(request)
    if username == user["username"]:
        raise ApiError(1005, "不能删除自己的账号", 400)
    u = db.query(Users).filter(Users.username == username).first()
    if u is None:
        raise ApiError(404, "用户不存在", 404)
    _last_active_admin_guard(db, username)
    crud_users.remove(db, id=u.id)
    _USER_STATUS_CACHE.pop(username, None)
    emit("认证", user["username"], f"删除账号 {username}")
    return ok({"deleted": username})


@app.post("/api/auth/users/{username}/reset-password")
async def auth_reset_password(username: str, request: Request, data: AuthResetPasswordIn, db: Session = Depends(get_db)):
    """管理员重置他人密码（新密码过策略）。"""
    from app.core.trace import emit

    user = _require_admin(request)
    body = data.model_dump()
    new_pw = body.get("new_password") or ""
    policy_error = validate_password(new_pw)
    if policy_error:
        raise ApiError(1004, policy_error, 400)
    u = db.query(Users).filter(Users.username == username).first()
    if u is None:
        raise ApiError(404, "用户不存在", 404)
    crud_users.update(db, db_obj=u, obj_in={"password_hash": hash_password(new_pw), "must_change": u.role != "admin"})  # admin 保留本地账号习惯；普通用户重置后强制修改
    from app.main import _USER_STATUS_CACHE

    _USER_STATUS_CACHE.pop(username, None)
    emit("认证", user["username"], f"重置账号 {username} 的密码（已标记待强制修改）")
    return ok({"username": username, "reset": True})
