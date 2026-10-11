"""数据表 defect_comments（缺陷评论流，Bugzilla longdescs 模式）。

带 type 打标：normal 人工评论 / system 系统动作评论（自动建单、回归结果、
同源复现）/ ai AI 修复建议。系统产生的动作都以带类型评论入流，人工与机器
一眼区分；缺陷详情页按时间线渲染。
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import _now


class DefectComments(Base):
    __tablename__ = "defect_comments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    defect_id: Mapped[int] = mapped_column(Integer, index=True)
    who: Mapped[str] = mapped_column(String(64), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    type: Mapped[str] = mapped_column(String(16), default="normal")  # normal|system|ai|dupe
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    __table_args__ = (Index("ix_defect_comments_defect_id_id", "defect_id", "id"),)
