"""认证域 API 请求模型（登录 / 改密 / 用户管理）。"""

from pydantic import BaseModel


class AuthLoginIn(BaseModel):
    username: str = ""
    password: str = ""


class AuthChangePasswordIn(BaseModel):
    old_password: str = ""
    new_password: str = ""


class AuthUserCreateIn(BaseModel):
    username: str = ""
    password: str = ""
    role: str = "viewer"


class AuthUserUpdateIn(BaseModel):
    """role/disabled 传 None 表示「不更新该字段」（两者都缺 → 路由层报 1001）。"""

    role: str | None = None
    disabled: bool | None = None


class AuthResetPasswordIn(BaseModel):
    new_password: str = ""
