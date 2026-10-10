"""需求域 API 请求模型（录入 / 冲突确认）。"""

from pydantic import BaseModel


class RequirementIngestIn(BaseModel):
    title: str = ""
    body: str = ""
    repo_id: int = 0
    source: str = "paste"


class RequirementConfirmIn(BaseModel):
    action: str = "approve"  # approve | reject
    note: str = ""
