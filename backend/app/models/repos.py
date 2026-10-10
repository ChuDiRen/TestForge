"""数据表 repos（Repos）。"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import _now


class Repos(Base):
    __tablename__ = "repos"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String(512))
    branch: Mapped[str] = mapped_column(String(128), default="main")
    credential_ref: Mapped[str] = mapped_column(String(256), default="")  # 凭据引用，不落明文
    last_pull: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="已接入")
    local_path: Mapped[str] = mapped_column(String(512), default="")
    head_rev: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
