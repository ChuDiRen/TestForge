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
        # users.disabled：账号停用（2026-10 认证加固新增）
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS disabled BOOLEAN NOT NULL DEFAULT FALSE"))
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change BOOLEAN NOT NULL DEFAULT FALSE"))
        # LightRAG 全量移植（2026-10）：KG 工作区 + 合并元数据 + 摄入管线状态机列
        conn.execute(text("ALTER TABLE kg_entities ADD COLUMN IF NOT EXISTS workspace VARCHAR(128) NOT NULL DEFAULT ''"))
        conn.execute(text("ALTER TABLE kg_entities ADD COLUMN IF NOT EXISTS etype_votes TEXT NOT NULL DEFAULT '{}'"))
        conn.execute(text("ALTER TABLE kg_entities ADD COLUMN IF NOT EXISTS desc_sources TEXT NOT NULL DEFAULT '{}'"))
        conn.execute(text("ALTER TABLE kg_relations ADD COLUMN IF NOT EXISTS workspace VARCHAR(128) NOT NULL DEFAULT ''"))
        conn.execute(text("ALTER TABLE kg_relations ADD COLUMN IF NOT EXISTS source_refs TEXT NOT NULL DEFAULT '[]'"))
        conn.execute(text("ALTER TABLE kg_relations ADD COLUMN IF NOT EXISTS desc_sources TEXT NOT NULL DEFAULT '{}'"))
        conn.execute(text("ALTER TABLE doc_status ADD COLUMN IF NOT EXISTS workspace VARCHAR(128) NOT NULL DEFAULT ''"))
        conn.execute(text("ALTER TABLE doc_status ADD COLUMN IF NOT EXISTS content_hash VARCHAR(64) NOT NULL DEFAULT ''"))
        # kg_entities 唯一键升级：repo 作用域 → (repo, workspace, name)（多工作区同 repo_id=0 隔离）
        conn.execute(text("DROP INDEX IF EXISTS ix_kg_entity_repo_name"))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_kg_entity_repo_ws_name ON kg_entities (repo_id, workspace, name)"))
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
        from services.shared.rag import ensure_pgvector_table, ensure_rag_documents_table

        ensure_pgvector_table()
        ensure_rag_documents_table()
    except Exception as exc:  # noqa: BLE001
        log.warning("rag table unavailable: %s", exc)


def db_ok() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False
