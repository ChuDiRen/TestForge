"""清空业务数据（保留表结构与 pgvector）：库中只留真实产生的数据。

用法：uv run python scripts/reset_db.py [--keep-schema-only]
默认 TRUNCATE 全部业务表并重置序列；data/runs 工作区一并清理。
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from app.db.session import get_session, init_db  # noqa: E402

TABLES = [
    "generation_events",
    "generations",
    "jobs",
    "trace_events",
    "defect_comments",
    "defect_activity",
    "defect_workflow",
    "defects",
    "iterations",
    "runs",
    "cases",
    "requirements",
    "contract_diffs",
    "contracts",
    "wiki_deps",
    "wiki_pages",
    "call_edges",
    "functions",
    "repos",
    # 知识/检索扩展表（混合检索、KG、影响面、聚类、LLM 缓存、文档状态）
    "rag_documents",
    "kg_entities",
    "kg_relations",
    "kg_communities",
    "kg_extractions",
    "kg_settings",
    "doc_status",
    "fn_impacts",
    "fn_clusters",
    "llm_cache",
    "embedding_cache",
    # AI 助手 / Wiki 对话（业务数据，users 登录账号保留）
    "chat_messages",
    "chat_threads",
    "wiki_chat_messages",
    "wiki_chat_sessions",
]


def _rmtree_force(path: Path) -> None:
    """Windows 下 .git pack 文件带只读位，rmtree 会静默残留 → 清只读位后强删。"""

    def _onerror(func, p, _exc):  # type: ignore[no-untyped-def]
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass

    shutil.rmtree(path, onerror=_onerror)


def main() -> int:
    init_db()
    with get_session() as sess:
        existing = set(
            sess.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            ).scalars()
        )
        targets = [t for t in TABLES if t in existing]
        skipped = [t for t in TABLES if t not in existing]
        if skipped:
            print(f"[reset] 库中不存在的表（跳过）：{', '.join(skipped)}")
        sess.execute(text(f"TRUNCATE TABLE {', '.join(targets)} RESTART IDENTITY CASCADE"))
        sess.commit()
    for sub in ("runs", "repos", "assistant_memory", "assistant_sandbox"):
        d = ROOT / "data" / sub
        if d.exists():
            _rmtree_force(d)
    print(f"[reset] 已清空 {len(TABLES)} 张业务表 + data/runs, repos, assistant_memory, assistant_sandbox（users 登录账号保留）")
    with get_session() as sess:
        repos = sess.execute(text("SELECT count(*) FROM repos")).scalar()
        cases = sess.execute(text("SELECT count(*) FROM cases")).scalar()
        print(f"[reset] 复核：repos={repos} cases={cases}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
