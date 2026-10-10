"""KG 向量库重建（LightRAG lightrag-rebuild-vdb 对齐）：embedding 后端/维度变更后重嵌全库。

用法：uv run python scripts/kg_rebuild_vdb.py [--batch 64]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.db.session import get_session, init_db  # noqa: E402
from app.services.knowledge.rag import ensure_rag_documents_table, tokenize  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=64)
    args = ap.parse_args()

    init_db()
    ensure_rag_documents_table()
    from app.services.knowledge.embedding import embed_batch

    done = 0
    with get_session() as sess:
        rows = sess.execute(text("SELECT doc_key, title, content FROM rag_documents WHERE embedding IS NULL ORDER BY updated_at")).all()
        print(f"待重嵌 {len(rows)} 行")
        for i in range(0, len(rows), args.batch):
            chunk = rows[i : i + args.batch]
            toks = [tokenize(f"{r[1]} {r[2]}") or r[2] for r in chunk]
            vecs = embed_batch(toks)
            for r, v in zip(chunk, vecs):
                import json as _json

                sess.execute(
                    text("UPDATE rag_documents SET embedding = CAST(:e AS vector) WHERE doc_key = :k"),
                    {"e": _json.dumps(v), "k": r[0]},
                )
            sess.commit()
            done += len(chunk)
            print(f"  {done}/{len(rows)}")
    print(f"完成：重嵌 {done} 行（embedding 缺失行清零）")


if __name__ == "__main__":
    main()
