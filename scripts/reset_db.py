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

from services.shared.config import get_settings  # noqa: E402
from services.shared.db import get_session, init_db  # noqa: E402

TABLES = [
    "generation_events",
    "generations",
    "trace_events",
    "defects",
    "iterations",
    "runs",
    "cases",
    "cases_embedding",
    "requirements",
    "contract_diffs",
    "contracts",
    "wiki_deps",
    "wiki_pages",
    "call_edges",
    "functions",
    "repos",
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
    url = get_settings().database_url
    with get_session() as sess:
        if url.startswith("sqlite"):
            for t in TABLES:
                try:
                    sess.execute(text(f"DELETE FROM {t}"))
                except Exception:  # noqa: BLE001
                    sess.rollback()  # pgvector 专有表在 sqlite 下不存在，跳过
            try:
                sess.execute(text("DELETE FROM sqlite_sequence"))
            except Exception:  # noqa: BLE001
                pass  # 库里还没有自增表时 sqlite_sequence 不存在
        else:
            sess.execute(text(f"TRUNCATE TABLE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
        sess.commit()
    for sub in ("runs", "repos"):
        d = ROOT / "data" / sub
        if d.exists():
            _rmtree_force(d)
    print("[reset] 已清空 16 张业务表 + data/runs + data/repos 检出缓存")
    with get_session() as sess:
        repos = sess.execute(text("SELECT count(*) FROM repos")).scalar()
        cases = sess.execute(text("SELECT count(*) FROM cases")).scalar()
        print(f"[reset] 复核：repos={repos} cases={cases}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
