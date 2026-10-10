"""数据表 requirements（Requirements）。"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import _now


class Requirements(Base):
    __tablename__ = "requirements"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(512))
    source: Mapped[str] = mapped_column(String(64), default="paste")  # md|docx|pdf|feishu|confluence|paste
    body: Mapped[str] = mapped_column(Text, default="")
    repo_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("repos.id"), nullable=True)
    testability_score: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32), default="解析中", index=True)
    trace_id: Mapped[str] = mapped_column(String(64), default="")
    parse_report: Mapped[str] = mapped_column(Text, default="")  # JSON
    quality_profile: Mapped[str] = mapped_column(Text, default="")  # JSON：G0~G6 档案
    manual_interventions: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
