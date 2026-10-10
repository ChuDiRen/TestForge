"""数据表 generations（Generations）。"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import _now


class Generations(Base):
    __tablename__ = "generations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # GEN-xxxx
    repo_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_function: Mapped[str] = mapped_column(String(256), default="")
    layer: Mapped[str] = mapped_column(String(16), default="ut")
    status: Mapped[str] = mapped_column(String(32), default="running")
    trace_id: Mapped[str] = mapped_column(String(64), default="")
    req_code: Mapped[str] = mapped_column(String(64), default="")
    plan_json: Mapped[str] = mapped_column(Text, default="")
    codegen: Mapped[str] = mapped_column(Text, default="")  # 最终 pytest 代码
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
