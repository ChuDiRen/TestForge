"""清理 KG 查询答案缓存（LightRAG lightrag-clean-llmqc 对齐）。

用法：uv run python scripts/kg_clean_cache.py [--all]   # --all 连抽取缓存一起清
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import init_db  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="连 LLM 抽取缓存一起清（下次全量重付 token）")
    args = ap.parse_args()

    init_db()
    from app.services.knowledge.kg_query import clear_query_cache

    n = clear_query_cache()
    print(f"查询答案缓存清理：{n} 条")
    if args.all:
        from app.services.knowledge.llm_cache import clear_cache

        print(f"LLM 抽取缓存清理：{clear_cache()} 条")


if __name__ == "__main__":
    main()
