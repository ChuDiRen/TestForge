"""数据表 wiki_pages（WikiPages）。"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import _now


class WikiPages(Base):
    __tablename__ = "wiki_pages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[int] = mapped_column(Integer, ForeignKey("repos.id"), index=True)
    level: Mapped[str] = mapped_column(String(16), index=True)  # repo|module|function|system
    title: Mapped[str] = mapped_column(String(256))
    content_md: Mapped[str] = mapped_column(Text, default="")
    rev: Mapped[int] = mapped_column(Integer, default=1)
    stale: Mapped[bool] = mapped_column(Boolean, default=False)
    module: Mapped[str] = mapped_column(String(256), default="", index=True)
    function: Mapped[str] = mapped_column(String(256), default="", index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
