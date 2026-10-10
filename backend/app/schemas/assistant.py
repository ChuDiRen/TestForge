"""AI 助手域 API 请求模型（会话创建 / 发消息）。"""

from pydantic import BaseModel


class AssistantThreadCreateIn(BaseModel):
    title: str = "新对话"
    repo_id: int = 0
    username: str = ""


class AssistantChatIn(BaseModel):
    thread_id: int = 0
    content: str = ""
