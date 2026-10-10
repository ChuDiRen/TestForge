"""数据表 jobs（Jobs）。"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import _now


class Jobs(Base):
    """任务队列：持久化任务（生成/回归），gateway 进程内 worker 消费，崩溃后 running 重排队。"""

    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # JOB-xxxx
    kind: Mapped[str] = mapped_column(String(32), index=True)  # generate|regression
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)  # queued|running|done|failed
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    result_json: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    gen_code: Mapped[str] = mapped_column(String(64), default="")  # 关联生成（generate 类）
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
