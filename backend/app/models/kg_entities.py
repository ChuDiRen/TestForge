"""数据表 kg_entities（KgEntity）。"""

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import _now


class KgEntity(Base):
    """文档级知识图谱实体（LightRAG 式）：从 wiki/需求/缺陷文本抽取。

    合并语义（LightRAG 三阶段对齐）：
    - etype_votes：type 投票计数 JSON {type: count}，多数票胜出；
    - desc_sources：各来源文档描述 JSON {source_ref: desc}，<8 条直接拼接、≥8 条 LLM 摘要；
    - source_refs：来源文档引用（选择性删除依据）。
    """

    __tablename__ = "kg_entities"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[int] = mapped_column(Integer, index=True, default=0)
    workspace: Mapped[str] = mapped_column(String(128), default="", index=True)  # 空=repo 作用域旧区
    name: Mapped[str] = mapped_column(String(256), index=True)
    etype: Mapped[str] = mapped_column(String(64), default="concept")  # module|function|concept|defect_pattern|...
    etype_votes: Mapped[str] = mapped_column(Text, default="{}")  # JSON {type: count}（type 投票）
    description: Mapped[str] = mapped_column(Text, default="")
    desc_sources: Mapped[str] = mapped_column(Text, default="{}")  # JSON {source_ref: description}
    source_refs: Mapped[str] = mapped_column(Text, default="[]")  # JSON array：来源文档引用（选择性删除依据）
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    __table_args__ = (Index("ix_kg_entity_repo_name", "repo_id", "name", unique=True),)
