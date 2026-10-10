"""数据表 chat_messages（ChatMessage）。"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import _now


class ChatMessage(Base):
    """AI 助手消息：role=user/assistant；工具调用过程与引用溯源以 JSON 留存。"""

    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[int] = mapped_column(Integer, index=True)
    role: Mapped[str] = mapped_column(String(16))  # user|assistant
    content: Mapped[str] = mapped_column(Text, default="")
    tool_events: Mapped[str] = mapped_column(Text, default="[]")  # [{name,args,summary,ms}]
    citations: Mapped[str] = mapped_column(Text, default="[]")  # [[path:12-34]] 解析结果
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
