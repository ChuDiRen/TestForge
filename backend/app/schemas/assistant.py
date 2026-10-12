"""AI 助手域 API 请求模型（会话创建 / 发消息）。"""

from pydantic import BaseModel, Field

from app.schemas.common import IdRef, RepoId


class AssistantThreadCreateIn(BaseModel):
    title: str = "新对话"
    repo_id: RepoId = 0
    username: str = ""


class AssistantChatIn(BaseModel):
    thread_id: IdRef  # 0=未指定会被业务拒绝，schema 直接收口
    content: str = Field(min_length=1)
