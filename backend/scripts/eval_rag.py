"""RAG 检索质量评估 CLI：黄金集 recall@k / MRR，混合检索 vs 向量单路对照。

用法：make rag-eval   或   uv run python scripts/eval_rag.py [k] [limit]
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.shared.db import init_db  # noqa: E402
from services.shared.rag_eval import evaluate  # noqa: E402


def main() -> int:
    init_db()
    k = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 5
    limit = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 50
    res = evaluate(k=k, limit=limit)
    if res.get("queries", 0) == 0:
        print(f"[WARN] {res.get('message', '无数据')}")
        return 1
    h, v = res["hybrid"], res["vector_only"]
    print(f"黄金集 {res['queries']} 条查询，k={k}")
    print(f"{'指标':<12}{'混合检索(向量+全文RRF)':<24}{'向量单路':<12}")
    print(f"{'recall@k':<14}{h['recall_at_k']:<26}{v['recall_at_k']:<12}")
    print(f"{'MRR':<14}{h['mrr']:<26}{v['mrr']:<12}")
    delta = round(h["recall_at_k"] - v["recall_at_k"], 4)
    print(f"\n混合检索 recall@k 提升：{delta:+.4f}")
    print("明细样例（前 5 条）：")
    for d in res["detail"][:5]:
        print(f"  Q: {d['query'][:50]:<52} hybrid={d['hybrid']} vector={d['vector']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
