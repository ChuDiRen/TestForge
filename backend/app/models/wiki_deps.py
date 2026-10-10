"""数据表 wiki_deps（WikiDeps）。"""

from sqlalchemy import ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class WikiDeps(Base):
    __tablename__ = "wiki_deps"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    page_id: Mapped[int] = mapped_column(Integer, ForeignKey("wiki_pages.id"), index=True)
    depends_on_page_id: Mapped[int] = mapped_column(Integer, ForeignKey("wiki_pages.id"))
