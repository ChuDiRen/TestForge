"""数据表 llm_cache（LlmCache）。"""

from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import _now


class LlmCache(Base):
    """LLM 抽取缓存：内容 hash 键控，增量重建只对变更输入重付 token。"""

    __tablename__ = "llm_cache"
    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256(role|model|system|prompt)
    role: Mapped[str] = mapped_column(String(16), default="extract")  # extract|query|keyword
    model: Mapped[str] = mapped_column(String(64), default="")
    prompt: Mapped[str] = mapped_column(Text, default="")  # 截断留存（审计用）
    response: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
