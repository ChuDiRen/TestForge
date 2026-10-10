"""数据表 contract_diffs（ContractDiffs）。"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import _now


class ContractDiffs(Base):
    __tablename__ = "contract_diffs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = mapped_column(Integer, ForeignKey("contracts.id"), index=True)
    from_v: Mapped[str] = mapped_column(String(64))
    to_v: Mapped[str] = mapped_column(String(64))
    breaking: Mapped[bool] = mapped_column(Boolean, default=False)
    detail: Mapped[str] = mapped_column(Text, default="")  # JSON
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
