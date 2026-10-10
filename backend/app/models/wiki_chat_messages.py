"""数据表 wiki_chat_messages（WikiChatMessage）。"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import _now


class WikiChatMessage(Base):
    """Wiki 问答消息：sources 存来源页（供追问指代解析与 qa_reference 边），
    source_mode = knowledge_base|no_data（no_data 服务端禁止存为知识文档）。"""

    __tablename__ = "wiki_chat_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(Integer, index=True)
    role: Mapped[str] = mapped_column(String(16))  # user|assistant
    content: Mapped[str] = mapped_column(Text, default="")
    sources_json: Mapped[str] = mapped_column(Text, default="[]")  # [{id,title,score}]
    source_mode: Mapped[str] = mapped_column(String(24), default="knowledge_base")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
