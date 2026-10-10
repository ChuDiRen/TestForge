"""数据表 call_edges（CallEdges）。"""

from sqlalchemy import ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class CallEdges(Base):
    __tablename__ = "call_edges"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    caller_id: Mapped[int] = mapped_column(Integer, ForeignKey("functions.id"), index=True)
    callee_id: Mapped[int] = mapped_column(Integer, ForeignKey("functions.id"), index=True)
