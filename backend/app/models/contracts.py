"""数据表 contracts（Contracts）。"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import _now


class Contracts(Base):
    __tablename__ = "contracts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), index=True)
    type: Mapped[str] = mapped_column(String(16))  # rest|grpc|topic
    provider_repo: Mapped[str] = mapped_column(String(256), default="")
    version: Mapped[str] = mapped_column(String(64), default="v1.0.0")
    spec: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="active")
    consumers: Mapped[str] = mapped_column(Text, default="")  # JSON array
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
