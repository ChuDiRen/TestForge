"""认证域 API 请求模型（登录 / 改密 / 用户管理）。

必填约束写进 schema（min_length=1 / 密码策略 pattern），由 FastAPI 统一 422，
不再靠路由层 400 兜底——契约测试对「schema 合法但业务拒绝」会按缺陷报。
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import StrictBool

NonEmptyStr = Annotated[str, Field(min_length=1)]
# 密码策略（与 core.auth.validate_password 同规）：≥8 位且同时含字母与数字。
# 不用 lookahead 写法，hypothesis 的正则生成器不支持。
Password = Annotated[str, Field(min_length=8, pattern=r".*[A-Za-z].*\d.*|.*\d.*[A-Za-z].*")]


class AuthLoginIn(BaseModel):
    username: NonEmptyStr
    password: NonEmptyStr


class AuthChangePasswordIn(BaseModel):
    old_password: NonEmptyStr
    new_password: Password


class AuthUserCreateIn(BaseModel):
    username: NonEmptyStr
    password: Password
    role: Literal["admin", "viewer"] = "viewer"


class AuthUserUpdateIn(BaseModel):
    """role/disabled 传 None 表示「不更新该字段」；两者都缺由模型校验拒绝（原路由层 1001）。

    anyOf-required 写进 JSON Schema（跨字段约束只有这里表达得了）：
    请求体必须至少出现 role 或 disabled 键，否则契约层即视为非法。
    """

    role: Literal["admin", "viewer"] | None = None
    disabled: StrictBool | None = None

    model_config = {
        "json_schema_extra": {"anyOf": [{"required": ["role"]}, {"required": ["disabled"]}]}
    }

    @model_validator(mode="after")
    def _at_least_one_key(self) -> "AuthUserUpdateIn":
        # json_schema_extra 只进 OpenAPI 不进运行时校验；这里按「键是否存在」拦 {}（全 null 放行为 no-op）
        if not (self.model_fields_set & {"role", "disabled"}):
            raise ValueError("role 与 disabled 至少传一项")
        return self


class AuthResetPasswordIn(BaseModel):
    new_password: Password
