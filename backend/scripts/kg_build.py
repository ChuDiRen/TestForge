"""文档知识图谱构建 CLI：wiki+缺陷+需求 → LLM 抽实体/关系（带缓存与增量跳过）。

用法：make kg-build   或   uv run python scripts/kg_build.py [repo_id]
前置：.env 配置 LLM_API_KEY（make llm-check 验证）；未配置时文档逐个记 failed 状态。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.shared.config import get_settings  # noqa: E402
from services.shared.db import get_session, init_db  # noqa: E402
from services.shared.docgraph import build_from_repo, kg_stats  # noqa: E402
from services.shared.docstatus import summary  # noqa: E402
from services.shared.models import Repos  # noqa: E402


def main() -> int:
    init_db()
    if not get_settings().llm_api_key:
        print("[FAIL] LLM_API_KEY 未配置：文档图谱抽取需要 LLM（make llm-check 验证）")
        return 1
    args = [a for a in sys.argv[1:] if a.isdigit()]
    with get_session() as sess:
        repo = sess.get(Repos, int(args[0])) if args else sess.query(Repos).order_by(Repos.id.desc()).first()
    if repo is None:
        print("[FAIL] 无仓库：先接入仓库")
        return 1
    print(f"repo#{repo.id} {repo.url} 开始构建文档知识图谱…")
    res = build_from_repo(repo.id)
    print(
        f"文档 {res['docs_total']}：构建 {res['docs_built']} / 增量跳过 {res['docs_skipped']} / 失败 {res['docs_failed']}"
    )
    stats = kg_stats(repo.id)
    print(f"图谱规模：实体 {stats['entities']} · 关系 {stats['relations']}")
    failed = [f for f in summary(repo.id)["recent_failures"] if f["kind"] == "kg"]
    if failed:
        print("失败文档：")
        for f in failed[:5]:
            print(f"  {f['doc_key']}: {f['error'][:80]}")
    print("\n[OK] 检索入口：GET /api/kg/query?q=...&mode=mix（local/global/mix）")
    return 0 if res["docs_failed"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
