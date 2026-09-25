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
        kwargs: dict = {"pool_pre_ping": True, "future": True, "pool_size": 5}
        kwargs["connect_args"] = {"connect_timeout": 5}  # PG 网络抖动快速失败，不悬挂事件循环
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
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_lock(728401)"))
        try:
            Base.metadata.create_all(conn)
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(728401)"))
        # 轻量列迁移：functions.language（多语言索引，2026-09 新增）
        conn.execute(text("ALTER TABLE functions ADD COLUMN IF NOT EXISTS language VARCHAR(32) NOT NULL DEFAULT ''"))
        # cases.stale：目标函数源码变更后待回归标记（变更驱动回归，2026-09 新增）
        conn.execute(text("ALTER TABLE cases ADD COLUMN IF NOT EXISTS stale BOOLEAN NOT NULL DEFAULT FALSE"))
        # defects.suggestion：AI 修复建议（DeepSeek 基于真实失败日志，2026-09 新增）
        conn.execute(text("ALTER TABLE defects ADD COLUMN IF NOT EXISTS suggestion TEXT NOT NULL DEFAULT ''"))
    # 首启引导管理员账号（幂等：仅当 users 为空）
    from services.shared.auth import bootstrap_admin

    bootstrap_admin()
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


def db_ok() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False
