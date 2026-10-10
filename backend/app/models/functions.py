"""数据表 functions（Functions）。"""

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Functions(Base):
    __tablename__ = "functions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[int] = mapped_column(Integer, ForeignKey("repos.id"), index=True)
    module: Mapped[str] = mapped_column(String(256), index=True)
    name: Mapped[str] = mapped_column(String(256), index=True)
    signature: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(Text, default="")
    file: Mapped[str] = mapped_column(String(512), default="")
    line: Mapped[int] = mapped_column(Integer, default=0)
    docstring: Mapped[str] = mapped_column(Text, default="")
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)
    language: Mapped[str] = mapped_column(String(32), default="")  # python|go|java|...（多语言索引）
