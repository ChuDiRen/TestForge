"""数据表 chat_threads（ChatThread）。"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import _now


class ChatThread(Base):
    """AI 助手会话（对标 GitNexus 对话：多轮代码问答，绑定仓库范围）。"""

    __tablename__ = "chat_threads"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(128), default="新对话")
    repo_id: Mapped[int] = mapped_column(Integer, default=0)  # 0=不限仓库
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
