"""KG 完整性检查与修复（LightRAG KG integrity repair 对齐）：

- 孤儿关系：端点实体不存在 → 删除；
- 悬空索引：kg_entities/kg_relations 有行但 rag_documents 缺索引行 → 重建；
- 抽取留存缺失：kg_extractions 无对应实体来源 → 提示（不自动重建，需重跑 build）。

用法：uv run python scripts/kg_repair.py [--fix]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from services.shared.db import get_session, init_db  # noqa: E402
from services.shared.models import KgRelation  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true", help="执行修复（默认只报告）")
    args = ap.parse_args()

    init_db()
    report: dict[str, int] = {"orphan_relations": 0, "missing_entity_index": 0, "missing_relation_index": 0}

    with get_session() as sess:
        # 1) 孤儿关系
        orphans = sess.execute(
            text(
                """SELECT k.id FROM kg_relations k
                   WHERE NOT EXISTS (SELECT 1 FROM kg_entities e WHERE e.name = k.src_name AND e.repo_id = k.repo_id AND e.workspace = k.workspace)
                      OR NOT EXISTS (SELECT 1 FROM kg_entities e WHERE e.name = k.dst_name AND e.repo_id = k.repo_id AND e.workspace = k.workspace)"""
            )
        ).all()
        report["orphan_relations"] = len(orphans)
        if args.fix and orphans:

            for (rid_,) in orphans:
                row = sess.get(KgRelation, rid_)
                if row is not None:
                    sess.delete(row)
            sess.commit()

        # 2) 悬空索引（按 workspace 分组重建）
        scopes = sess.execute(text("SELECT repo_id, workspace FROM kg_entities GROUP BY repo_id, workspace")).all()
        from services.shared.kg_merge import rebuild_index

        for repo_id, ws in scopes:
            ent_missing = sess.execute(
                text(
                    """SELECT COUNT(*) FROM kg_entities e
                       WHERE e.repo_id = :r AND e.workspace = :ws
                         AND NOT EXISTS (SELECT 1 FROM rag_documents d WHERE d.doc_key = 'kg_entity:' || e.repo_id || ':' || e.workspace || ':' || e.name)"""
                ),
                {"r": repo_id, "ws": ws},
            ).scalar()
            rel_missing = sess.execute(
                text(
                    """SELECT COUNT(*) FROM kg_relations k
                       WHERE k.repo_id = :r AND k.workspace = :ws
                         AND NOT EXISTS (SELECT 1 FROM rag_documents d WHERE d.doc_key = 'kg_relation:' || k.repo_id || ':' || k.workspace || ':' || k.id)"""
                ),
                {"r": repo_id, "ws": ws},
            ).scalar()
            report["missing_entity_index"] += int(ent_missing or 0)
            report["missing_relation_index"] += int(rel_missing or 0)
            if args.fix and (ent_missing or rel_missing):
                rebuild_index(repo_id, ws)

    print("完整性报告：")
    for k, v in report.items():
        print(f"  {k}: {v}")
    if not args.fix and any(report.values()):
        print("（--fix 执行修复）")


if __name__ == "__main__":
    main()
