"""数据表 doc_status（DocStatus）。"""

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import _now


class DocStatus(Base):
    """知识摄入文档状态跟踪（LightRAG 异步状态机：pending → processing → ok/failed）。

    legacy kind（index/wiki/kg/fts/rag/user_doc）保持同步 ok|failed|stale 写入；
    kind='kg_doc' 为 LightRAG 式异步摄入管线（工作台分块→抽取→合并→索引）专用。
    """

    __tablename__ = "doc_status"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[int] = mapped_column(Integer, index=True, default=0)
    workspace: Mapped[str] = mapped_column(String(128), default="", index=True)  # 空=repo 作用域旧区
    kind: Mapped[str] = mapped_column(String(32), index=True)  # index|wiki|kg|fts|rag|kg_doc
    doc_key: Mapped[str] = mapped_column(String(512), default="")
    status: Mapped[str] = mapped_column(String(16), default="ok", index=True)  # pending|processing|ok|failed|stale
    error: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[str] = mapped_column(Text, default="")  # JSON：chunks/extract/strategy/file 等进度元数据
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    __table_args__ = (Index("ix_doc_status_key", "repo_id", "kind", "doc_key", unique=True),)
