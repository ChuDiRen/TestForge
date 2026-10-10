"""索引期预计算按需入口：影响面（blast radius）+ 功能聚类（测试域）。

用法：make analyze   或   uv run python scripts/repo_analyze.py [repo_id]
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db.session import get_session, init_db  # noqa: E402
from app.models import Repos  # noqa: E402
from app.services.repo.clusters import list_clusters  # noqa: E402
from app.services.repo.clusters import recompute as recompute_clusters  # noqa: E402
from app.services.repo.impact import recompute as recompute_impact  # noqa: E402
from app.services.repo.impact import top_impact  # noqa: E402


def main() -> int:
    init_db()
    args = [a for a in sys.argv[1:] if a.isdigit()]
    with get_session() as sess:
        repo = sess.get(Repos, int(args[0])) if args else sess.query(Repos).order_by(Repos.id.desc()).first()
    if repo is None:
        print("[FAIL] 无仓库：先接入仓库（make demo-m1 / POST /api/repos）")
        return 1
    print(f"repo#{repo.id} {repo.url}")
    n = recompute_impact(repo.id)
    print(f"[1/2] 影响面预计算完成：{n} 函数；Top5 blast radius：")
    for t in top_impact(repo.id, 5):
        print(f"    {t['name']:<40} score={t['score']:<8} 反向可达 {t['reach_count']} 函数 / 深度 {t['depth_reached']}")
    c = recompute_clusters(repo.id)
    clusters = list_clusters(repo.id)
    print(f"[2/2] 功能聚类完成：{c.get('clusters', 0)} 个测试域：")
    for cl in clusters[:10]:
        print(f"    [{cl['label']}] {cl['size']} 函数（如 {', '.join(cl['functions'][:4])}）")
    print("\n[OK] 预计算物已入库：/api/functions/{name}/impact、/api/repos/{id}/clusters、/api/graph 节点带影响分")
    return 0


if __name__ == "__main__":
    sys.exit(main())
