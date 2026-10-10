"""数据表 runs（Runs）。"""

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import _now


class Runs(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # RUN-xxxx
    target: Mapped[str] = mapped_column(String(256))
    layer: Mapped[str] = mapped_column(String(16))
    trigger: Mapped[str] = mapped_column(String(32), default="手动")
    sandbox_status: Mapped[str] = mapped_column(String(32), default="pending")
    pass_total: Mapped[int] = mapped_column(Integer, default=0)
    pass_count: Mapped[int] = mapped_column(Integer, default=0)
    coverage: Mapped[float] = mapped_column(Float, default=0.0)
    repair_rounds: Mapped[int] = mapped_column(Integer, default=0)
    cost_s: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)
    trace_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    req_code: Mapped[str] = mapped_column(String(64), default="")
    gen_id: Mapped[str] = mapped_column(String(64), default="")
    log_json: Mapped[str] = mapped_column(Text, default="")  # 时间线/用例结果 JSON
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
