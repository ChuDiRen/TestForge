"""数据表 kg_relations（KgRelation）。"""

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import _now


class KgRelation(Base):
    """知识图谱关系边：src/dst 为 KgEntity.name；weight=证据计数（支持该边的文档数）。"""

    __tablename__ = "kg_relations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[int] = mapped_column(Integer, index=True, default=0)
    workspace: Mapped[str] = mapped_column(String(128), default="", index=True)  # 空=repo 作用域旧区
    src_name: Mapped[str] = mapped_column(String(256), index=True)
    dst_name: Mapped[str] = mapped_column(String(256), index=True)
    rtype: Mapped[str] = mapped_column(String(64), default="related")
    description: Mapped[str] = mapped_column(Text, default="")
    desc_sources: Mapped[str] = mapped_column(Text, default="{}")  # JSON {source_ref: description}
    weight: Mapped[float] = mapped_column(Float, default=1.0)  # 证据计数（source_refs 长度）
    source_ref: Mapped[str] = mapped_column(String(256), default="")  # 首个来源（兼容旧读方）
    source_refs: Mapped[str] = mapped_column(Text, default="[]")  # JSON array：全部证据来源
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
