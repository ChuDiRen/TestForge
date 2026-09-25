"""SQLAlchemy 引擎/会话 + 建表。"""

import logging

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from services.shared.config import get_settings

log = logging.getLogger("shared.db")


class Base(DeclarativeBase):
    pass


_engine = None
_factory = None


def get_engine():
    global _engine, _factory
    if _engine is None:
        url = get_settings().database_url
        kwargs: dict = {"pool_pre_ping": True, "future": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        else:
            kwargs["pool_size"] = 5
        _engine = create_engine(url, **kwargs)
        _factory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_session():
    get_engine()
    assert _factory is not None
    return _factory()


def init_db() -> None:
    """建全部表（幂等；advisory lock 防 7 服务并发 DDL 竞态）。"""
    from services.shared import models  # noqa: F401  确保模型注册

    engine = get_engine()
    if not get_settings().database_url.startswith("sqlite"):
        with engine.begin() as conn:
            conn.execute(text("SELECT pg_advisory_lock(728401)"))
            try:
                Base.metadata.create_all(conn)
            finally:
                conn.execute(text("SELECT pg_advisory_unlock(728401)"))
            # 轻量列迁移：functions.language（多语言索引，2026-09 新增）
            conn.execute(text("ALTER TABLE functions ADD COLUMN IF NOT EXISTS language VARCHAR(32) NOT NULL DEFAULT ''"))
        try:
            with engine.begin() as conn:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            log.info("pgvector extension ready")
        except Exception as exc:  # noqa: BLE001
            log.warning("pgvector unavailable: %s", exc)
        try:
            from services.shared.rag import ensure_pgvector_table

            ensure_pgvector_table()
        except Exception as exc:  # noqa: BLE001
            log.warning("rag table unavailable: %s", exc)
    else:
        Base.metadata.create_all(engine)


def db_ok() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False
