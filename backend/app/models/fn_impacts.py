"""数据表 fn_impacts（FnImpact）。"""

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import _now


class FnImpact(Base):
    """索引期预计算影响面（blast radius）：反向可达集 + 调用深度 + 置信度。"""

    __tablename__ = "fn_impacts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[int] = mapped_column(Integer, index=True)
    fn_name: Mapped[str] = mapped_column(String(256), index=True)
    callers_count: Mapped[int] = mapped_column(Integer, default=0)  # 直接调用方数
    callees_count: Mapped[int] = mapped_column(Integer, default=0)  # 直接依赖数
    reach_count: Mapped[int] = mapped_column(Integer, default=0)  # 反向可达集大小（改它炸多大）
    depth_reached: Mapped[int] = mapped_column(Integer, default=0)  # 反向传播最深层
    score: Mapped[float] = mapped_column(Float, default=0.0)  # 影响分（可达集/全函数归一）
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    __table_args__ = (Index("ix_fn_impact_repo_fn", "repo_id", "fn_name", unique=True),)
