"""数据表 defect_activity（字段级变更日志，Bugzilla bugs_activity 模式）。

一次变更 = 一行 diff：谁、何时、哪个字段、旧值→新值。所有缺陷写路径统一
经 defect_svc.update_defect / _log_activity 落这里，前端"操作记录"直接可读。
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import _now


class DefectActivity(Base):
    __tablename__ = "defect_activity"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    defect_id: Mapped[int] = mapped_column(Integer, index=True)
    actor: Mapped[str] = mapped_column(String(64), default="")  # 操作人（qa/deepseek/runner-svc）
    field: Mapped[str] = mapped_column(String(64), default="")  # 状态/结案原因/优先级/重复指向...
    old_value: Mapped[str] = mapped_column(Text, default="")
    new_value: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    __table_args__ = (Index("ix_defect_activity_defect_id_id", "defect_id", "id"),)
