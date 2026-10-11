"""数据表 defects（Defects）。"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import _now


class Defects(Base):
    __tablename__ = "defects"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # BUG-xxx
    title: Mapped[str] = mapped_column(String(512))
    origin_run: Mapped[str] = mapped_column(String(64), default="")
    case_codes: Mapped[str] = mapped_column(Text, default="[]")  # JSON array
    req_code: Mapped[str] = mapped_column(String(64), default="")
    severity: Mapped[str] = mapped_column(String(16), default="严重")
    status: Mapped[str] = mapped_column(String(32), default="新建", index=True)
    assignee: Mapped[str] = mapped_column(String(64), default="")
    trace_id: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    suggestion: Mapped[str] = mapped_column(Text, default="")  # AI 修复建议（Markdown，DeepSeek 基于真实失败日志生成）
    # 结案原因（Jira Resolution 语义）：已修复/非缺陷/重复/无法复现/暂缓——关闭时必填，重开自动清空
    resolution: Mapped[str] = mapped_column(String(32), default="", index=True)
    # 修复优先级（P0-P3，人排的处置顺序，与机器判定的 severity 分离）
    priority: Mapped[str] = mapped_column(String(8), default="")
    # 修复版本：计划随哪个迭代/版本交付（Jira Fix Version，缺陷↔迭代追溯链）
    fix_version: Mapped[str] = mapped_column(String(64), default="")
    # 重复指向：标记为重复时指向的目标缺陷编码（dupe 链落库前扁平化到最终目标，防环）
    dupe_of: Mapped[str] = mapped_column(String(64), default="")
    # 最后更新时间（乐观锁基准：更新请求带 expected_updated_at，不匹配拒绝写入）
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
