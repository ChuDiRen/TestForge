"""数据表 trace_events（TraceEvents）。"""

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import _now


class TraceEvents(Base):
    __tablename__ = "trace_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    type: Mapped[str] = mapped_column(String(16))  # 需求|生成|执行|仓库|契约|缺陷|计划
    actor: Mapped[str] = mapped_column(String(64), default="system")
    summary: Mapped[str] = mapped_column(Text, default="")
    req_code: Mapped[str] = mapped_column(String(64), default="", index=True)
    extra: Mapped[str] = mapped_column(Text, default="{}")


Index("ix_trace_events_trace", TraceEvents.trace_id, TraceEvents.ts)
