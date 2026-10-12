"""用例域 API 请求模型（元数据编辑 / 人审）。"""

from typing import Literal

from pydantic import BaseModel, model_validator

from app.schemas.common import StrictBool, StrictFloat


class CaseUpdateIn(BaseModel):
    """PATCH 语义：仅客户端显式传入的字段参与更新（路由层用 exclude_unset 还原）。

    全空更新由模型校验拒绝（原路由层 1001「无可更新字段」）。
    """

    title: str | None = None
    status: Literal["草稿", "待人审", "已入库", "已替换"] | None = None
    review_note: str | None = None
    confidence: StrictFloat | None = None
    stale: StrictBool | None = None

    model_config = {
        # 同 AuthUserUpdateIn：至少出现一个更新键（显式 null 允许，路由层按「忽略」处理）
        "json_schema_extra": {
            "anyOf": [{"required": [k]} for k in ("title", "status", "review_note", "confidence", "stale")]
        }
    }

    @model_validator(mode="after")
    def _at_least_one_key(self) -> "CaseUpdateIn":
        # json_schema_extra 只进 OpenAPI 不进运行时校验；这里按「键是否存在」拦 {}（全 null 放行为 no-op）
        if not (self.model_fields_set & {"title", "status", "review_note", "confidence", "stale"}):
            raise ValueError("title/status/review_note/confidence/stale 至少传一项")
        return self


class CaseReviewIn(BaseModel):
    action: str = ""  # approve | reject（其余值按 reject 处理）
    note: str = ""
