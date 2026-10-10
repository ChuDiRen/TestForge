"""数据表 defects（Defects）。"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import _now


class Defects(Base):
    __tablename__ = "defects"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # BUG-xxx
    title: Mapped[str] = mapped_column(String(512))
    origin_run: Mapped[str] = mapped_column(String(64), default="")
    case_codes: Mapped[str] = mapped_column(Text, default="[]")  # JSON array
    req_code: Mapped[str] = mapped_column(String(64), default="")
    severity: Mapped[str] = mapped_column(String(16), default="严重")
    status: Mapped[str] = mapped_column(String(32), default="新建", index=True)
    assignee: Mapped[str] = mapped_column(String(64), default="")
    trace_id: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    suggestion: Mapped[str] = mapped_column(Text, default="")  # AI 修复建议（Markdown，DeepSeek 基于真实失败日志生成）
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
