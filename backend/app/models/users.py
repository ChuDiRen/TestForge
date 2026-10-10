"""数据表 users（Users）。"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import _now


class Users(Base):
    """平台账号：admin 全权；viewer 只读（GET）。disabled 停用后登录/请求一律拒绝。"""

    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256), default="")  # PBKDF2-SHA256 salt$digest
    role: Mapped[str] = mapped_column(String(16), default="viewer")  # admin|viewer
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)  # 停用账号（离职/临时封禁）
    must_change: Mapped[bool] = mapped_column(Boolean, default=False)  # 待强制改密（默认口令/管理员重置后）
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
