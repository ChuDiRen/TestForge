"""数据表 kg_communities（KgCommunity）。"""

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import _now


class KgCommunity(Base):
    """KG 社区（Louvain）+ 社区报告：map-reduce LLM 摘要，global 查询的主题级弹药库。"""

    __tablename__ = "kg_communities"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[int] = mapped_column(Integer, index=True, default=0)
    workspace: Mapped[str] = mapped_column(String(128), default="", index=True)
    level: Mapped[int] = mapped_column(Integer, default=1, index=True)  # 1=基础社区 2=社区 的社区（reduce 层）
    cluster_id: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(256), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    members: Mapped[str] = mapped_column(Text, default="[]")  # JSON array：成员实体名
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    summary_source: Mapped[str] = mapped_column(String(16), default="llm")  # llm|concat（无 Key 时的确定性回退）
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    __table_args__ = (Index("ix_kg_comm_scope", "repo_id", "workspace", "level", "cluster_id"),)
