"""数据表 embedding_cache（EmbeddingCache）。"""

from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import _now


class EmbeddingCache(Base):
    """神经 embedding 持久缓存：同 (backend, model, text) 只付一次钱，重启不失效。"""

    __tablename__ = "embedding_cache"
    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256(backend|model|text)
    model: Mapped[str] = mapped_column(String(128), default="")
    vec: Mapped[str] = mapped_column(Text, default="")  # JSON array[float]
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
