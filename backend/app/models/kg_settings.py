"""数据表 kg_settings（KgSetting）。"""

from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import _now


class KgSetting(Base):
    """运行时检索参数覆盖（设置页可改，优先于 config .env 默认值）。"""

    __tablename__ = "kg_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")  # JSON 标量
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
