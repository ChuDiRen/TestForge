"""用例域 API 请求模型（元数据编辑 / 人审）。"""

from pydantic import BaseModel


class CaseUpdateIn(BaseModel):
    """PATCH 语义：仅客户端显式传入的字段参与更新（路由层用 exclude_unset 还原）。"""

    title: str | None = None
    status: str | None = None
    review_note: str | None = None
    confidence: float | None = None
    stale: bool | None = None


class CaseReviewIn(BaseModel):
    action: str = ""  # approve | reject（其余值按 reject 处理）
    note: str = ""
