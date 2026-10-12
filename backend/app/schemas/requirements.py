"""需求域 API 请求模型（录入 / 冲突确认）。"""

from pydantic import BaseModel, Field

from app.schemas.common import RepoId


class RequirementIngestIn(BaseModel):
    title: str = Field(min_length=1)
    body: str = Field(min_length=1)
    repo_id: RepoId = 0
    source: str = "paste"


class RequirementConfirmIn(BaseModel):
    action: str = "approve"  # approve | reject
    note: str = ""
