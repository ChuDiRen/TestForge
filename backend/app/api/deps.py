"""FastAPI 依赖注入（标准件）：请求级数据库会话，路由用 Depends(get_db) 获取。

事务口径与 with get_session() 一致：由路由/服务层显式 commit；
请求结束自动 close，不自动回滚（与既有业务语义对齐）。
"""

from collections.abc import Generator

from sqlalchemy.orm import Session

from app.db.session import get_session


def get_db() -> Generator[Session, None, None]:
    db = get_session()
    try:
        yield db
    finally:
        db.close()
