"""数据表 cases（Cases）。"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import _now


class Cases(Base):
    __tablename__ = "cases"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    layer: Mapped[str] = mapped_column(String(16), index=True)  # ut|api|fn|e2e|contract
    title: Mapped[str] = mapped_column(String(512))
    module: Mapped[str] = mapped_column(String(256), default="", index=True)
    category: Mapped[str] = mapped_column(String(32), default="normal", index=True)  # normal|boundary|exception|permission|contract
    schema_json: Mapped[str] = mapped_column(Text, default="")
    source_req: Mapped[str] = mapped_column(String(64), default="", index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.9)
    status: Mapped[str] = mapped_column(String(32), default="草稿", index=True)  # 草稿|待人审|已入库
    review_note: Mapped[str] = mapped_column(Text, default="")
    trace_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    gen_id: Mapped[str] = mapped_column(String(64), default="")
    last_run_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    target_function: Mapped[str] = mapped_column(String(256), default="")
    repo_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stale: Mapped[bool] = mapped_column(Boolean, default=False)  # 目标函数源码已变更，待回归确认
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
