"""数据表 defect_workflow（状态转换图，Bugzilla status_workflow + Jira validator 语义）。

数据驱动而非硬编码：from_status 为空表示"建单时"的初始转换；require_comment
为真时该转换必须伴随评论（关闭/重开）；管理员后续可直接改表调整流程，无需发版。
服务端按此表校验流转合法性，防死胡同：种子图保证每个状态都有出边。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import _now


class DefectWorkflow(Base):
    __tablename__ = "defect_workflow"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)  # NULL=建单初始
    to_status: Mapped[str] = mapped_column(String(32))
    require_comment: Mapped[bool] = mapped_column(Boolean, default=False)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
