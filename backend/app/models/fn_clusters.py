"""数据表 fn_clusters（FnCluster）。"""

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import _now


class FnCluster(Base):
    """功能聚类（Louvain 社区检测）：自动划分测试域。"""

    __tablename__ = "fn_clusters"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[int] = mapped_column(Integer, index=True)
    fn_name: Mapped[str] = mapped_column(String(256), index=True)
    cluster_id: Mapped[int] = mapped_column(Integer, default=0)
    label: Mapped[str] = mapped_column(String(128), default="")  # 测试域名（主导模块前缀/枢纽函数）
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    __table_args__ = (Index("ix_fn_cluster_repo_fn", "repo_id", "fn_name", unique=True),)
