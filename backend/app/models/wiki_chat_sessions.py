"""数据表 wiki_chat_sessions（WikiChatSession）。"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import _now


class WikiChatSession(Base):
    """Wiki 问答会话（OpenWiki wiki_chat_sessions 对等物）：按仓库分组的多会话。"""

    __tablename__ = "wiki_chat_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[int] = mapped_column(Integer, default=0, index=True)
    title: Mapped[str] = mapped_column(String(256), default="新问答")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
