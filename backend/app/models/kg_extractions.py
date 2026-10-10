"""数据表 kg_extractions（KgExtraction）。"""

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import _now


class KgExtraction(Base):
    """每份源文档的抽取结果留存（LightRAG 删除文档→用缓存重建图谱描述的依据）。"""

    __tablename__ = "kg_extractions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[int] = mapped_column(Integer, index=True, default=0)
    workspace: Mapped[str] = mapped_column(String(128), default="", index=True)
    source_ref: Mapped[str] = mapped_column(String(256), index=True)  # 文档引用（kgdoc:xx / wiki:1 / defect:BUG-x）
    doc_key: Mapped[str] = mapped_column(String(512), default="")  # rag_documents 主文档 doc_key（kg_doc 管线用）
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    result_json: Mapped[str] = mapped_column(Text, default="{}")  # 全 chunk 合并后的抽取结果
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    gleaning_rounds: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    __table_args__ = (Index("ix_kg_ext_scope_ref", "repo_id", "workspace", "source_ref"),)
