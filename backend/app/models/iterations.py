"""数据表 iterations（Iterations）。"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import _now


class Iterations(Base):
    __tablename__ = "iterations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # ITER-xxx
    version: Mapped[str] = mapped_column(String(64), default="")
    req_codes: Mapped[str] = mapped_column(Text, default="[]")  # JSON array
    case_stats: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    entry_status: Mapped[str] = mapped_column(String(32), default="待准入")
    exit_status: Mapped[str] = mapped_column(String(32), default="待准出")
    report: Mapped[str] = mapped_column(Text, default="")  # JSON
    trace_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
