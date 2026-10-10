"""需求域 API 请求模型（录入 / 冲突确认）。"""

from pydantic import BaseModel


class RequirementIngestIn(BaseModel):
    title: str = ""
    body: str = ""
    text: str = ""  # body 的兼容别名（二选一）
    repo_id: int = 0
    source: str = "paste"


class RequirementConfirmIn(BaseModel):
    action: str = "approve"  # approve | reject
    note: str = ""
