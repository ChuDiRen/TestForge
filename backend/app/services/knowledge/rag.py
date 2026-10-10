"""混合检索：pgvector 向量 + PostgreSQL tsvector 全文，RRF（Reciprocal Rank Fusion）融合。

借鉴 GitNexus（BM25+语义+RRF）与 LightRAG（双层检索）：
- 统一文档表 rag_documents：kind 区分 case|wiki|defect|requirement|function|kg_entity|kg_relation|kg_chunk|kg_community；
- workspace 列隔离 LightRAG 式多工作区（空 = repo 作用域旧区，按 repo_id 过滤）；
- 检索 = 向量近邻 + 全文 ts_rank 两路召回，RRF 融合排序（k=60），可选 rerank 后处理；
- updated_after：时间窗口过滤（OpenWiki time_filter 移植）；
- 无 pgvector 时回退 python 余弦 + 词面重合打分，接口不变。
embedding 委托 app.services.knowledge.embedding：local hash 词袋（默认零依赖）或 OpenAI 兼容神经向量。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime

from app.db.session import get_session

log = logging.getLogger("shared.rag")

DIM = 256
_TOKEN = re.compile(r"[A-Za-z_]{2,}|[\u4e00-\u9fff]")
_RRF_K = 60


_EMBED_CACHE: dict[str, list[float]] = {}
_EMBED_CACHE_MAX = 10_000


def _embed_cached(content: str) -> list[float]:
    key = hashlib.md5(content.encode("utf-8")).hexdigest()
    vec = _EMBED_CACHE.get(key)
    if vec is None:
        vec = embed(content)
        if len(_EMBED_CACHE) >= _EMBED_CACHE_MAX:
            _EMBED_CACHE.clear()  # 简单防膨胀：满则整体换血
        _EMBED_CACHE[key] = vec
    return vec


def embed(text: str) -> list[float]:
    """词袋 hash 向量（委托 embedding 模块 local 后端；历史调用方 rag.embed 语义不变）。"""
    from app.services.knowledge.embedding import _hash_vec

    return _hash_vec(text)


def tokenize(text: str) -> str:
    """全文检索分词：拉丁词原样 + CJK 单字双元（PG 'simple' 不做中文切分）。"""
    text = (text or "").lower()
    latin = [t for t in re.findall(r"[A-Za-z_]{2,}", text)]
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    bigrams = [cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)] or (cjk[:1] if cjk else [])
    return " ".join(latin + bigrams)


def _fts_query(tokens: str) -> str:
    """token 串 → tsquery OR 表达式（过滤非法字符防语法错）。"""
    parts = [t for t in tokens.split() if re.fullmatch(r"[a-z_][a-z0-9_]*|[\u4e00-\u9fff]{1,2}", t)]
    return " | ".join(parts) if parts else ""


def _tables_ready(sess) -> bool:  # type: ignore[no-untyped-def]
    try:
        from sqlalchemy import text

        sess.execute(text("SELECT 1 FROM rag_documents LIMIT 1"))
        return True
    except Exception:  # noqa: BLE001
        sess.rollback()
        return False


def ensure_rag_documents_table() -> None:
    """统一检索表（幂等）：向量 + 全文双列，GIN 索引，workspace 列。

    向量维度跟随 embedding 后端：切换神经后端维度变化时 ALTER 列并清空旧向量
    （跑 make kg-rebuild-vdb 重嵌全库）。
    """
    from sqlalchemy import text

    from app.db.session import get_engine
    from app.services.knowledge import embedding as emb

    dim = emb.dim()
    try:
        with get_engine().begin() as conn:
            conn.execute(
                text(
                    f"""CREATE TABLE IF NOT EXISTS rag_documents (
                        doc_key TEXT PRIMARY KEY,
                        kind TEXT NOT NULL DEFAULT '',
                        repo_id INTEGER NOT NULL DEFAULT 0,
                        workspace TEXT NOT NULL DEFAULT '',
                        title TEXT NOT NULL DEFAULT '',
                        content TEXT NOT NULL DEFAULT '',
                        fts tsvector,
                        embedding vector({dim}),
                        meta JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )"""
                )
            )
            conn.execute(text("ALTER TABLE rag_documents ADD COLUMN IF NOT EXISTS workspace TEXT NOT NULL DEFAULT ''"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_rag_documents_kind ON rag_documents(kind)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_rag_documents_repo ON rag_documents(repo_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_rag_documents_ws ON rag_documents(workspace)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_rag_documents_fts ON rag_documents USING GIN(fts)"))
            # 向量维度迁移：列维度 ≠ 当前后端维度 → ALTER + 清空旧向量（内容仍在，可重嵌）
            cur = conn.execute(
                text("SELECT atttypmod FROM pg_attribute WHERE attrelid = 'rag_documents'::regclass AND attname = 'embedding'")
            ).scalar()
            if cur is not None and int(cur) != dim:
                conn.execute(text(f"ALTER TABLE rag_documents ALTER COLUMN embedding TYPE vector({dim})"))
                conn.execute(text("UPDATE rag_documents SET embedding = NULL"))
                log.warning("rag_documents 向量维度迁移 %s -> %s，旧向量已清空（请跑 kg-rebuild-vdb 重嵌）", int(cur), dim)
        log.info("rag_documents table ready (hybrid检索, dim=%s)", dim)
    except Exception as exc:  # noqa: BLE001
        log.warning("rag_documents table unavailable: %s", exc)


def index_document(
    doc_key: str, kind: str, title: str, content: str, repo_id: int = 0, meta: dict | None = None, workspace: str = ""
) -> None:
    """文档入库/更新（upsert）：全文 + 向量双索引。workspace 非空时走 LightRAG 工作区隔离。"""
    tokens = tokenize(f"{title} {content}")
    vec = _embed_cached(tokens or content)
    with get_session() as sess:
        if not _tables_ready(sess):
            return
        from sqlalchemy import text

        sess.execute(
            text(
                """INSERT INTO rag_documents (doc_key, kind, repo_id, workspace, title, content, fts, embedding, meta, updated_at)
                   VALUES (:k, :kind, :rid, :ws, :t, :c, to_tsvector('simple', :tok), CAST(:e AS vector), CAST(:meta AS jsonb), now())
                   ON CONFLICT (doc_key) DO UPDATE SET
                     kind = EXCLUDED.kind, repo_id = EXCLUDED.repo_id, workspace = EXCLUDED.workspace,
                     title = EXCLUDED.title, content = EXCLUDED.content, fts = EXCLUDED.fts,
                     embedding = EXCLUDED.embedding, meta = EXCLUDED.meta, updated_at = now()"""
            ),
            {
                "k": doc_key,
                "kind": kind,
                "rid": repo_id,
                "ws": workspace,
                "t": title[:500],
                "c": content[:20000],
                "tok": tokens[:6000],
                "e": json.dumps(vec),
                "meta": json.dumps(meta or {}, ensure_ascii=False),
            },
        )
        sess.commit()


def remove_document(doc_key: str) -> None:
    with get_session() as sess:
        if not _tables_ready(sess):
            return
        from sqlalchemy import text

        sess.execute(text("DELETE FROM rag_documents WHERE doc_key = :k"), {"k": doc_key})
        sess.commit()


def remove_documents_by_prefix(prefix: str) -> int:
    with get_session() as sess:
        if not _tables_ready(sess):
            return 0
        from sqlalchemy import text

        n = sess.execute(text("DELETE FROM rag_documents WHERE doc_key LIKE :p"), {"p": prefix + "%"}).rowcount
        sess.commit()
        return int(n or 0)


def remove_documents_scope(kind: str, workspace: str) -> int:
    """按 workspace 清空某 kind（LightRAG 工作区级替换，kg_chunk/kg_community 重建用）。"""
    with get_session() as sess:
        if not _tables_ready(sess):
            return 0
        from sqlalchemy import text

        n = sess.execute(
            text("DELETE FROM rag_documents WHERE kind = :k AND workspace = :ws"), {"k": kind, "ws": workspace}
        ).rowcount
        sess.commit()
        return int(n or 0)


def index_documents_bulk(
    items: list[dict], replace_scope: tuple[str, int] | None = None, replace_scope_ws: tuple[str, str] | None = None
) -> int:
    """批量原子索引：单事务先删旧文档再插入（copy-and-swap）。

    items: [{doc_key, kind, repo_id, title, content, meta?, workspace?}]。
    replace_scope=(kind, repo_id)：repo 作用域替换（workspace=''）；
    replace_scope_ws=(kind, workspace)：工作区作用域替换。任何一行失败整体回滚。
    """
    if not items and not replace_scope and not replace_scope_ws:
        return 0
    with get_session() as sess:
        if not _tables_ready(sess):
            return 0
        from sqlalchemy import text

        try:
            if replace_scope:
                kind, rid = replace_scope
                sess.execute(
                    text("DELETE FROM rag_documents WHERE kind = :k AND repo_id = :r AND workspace = ''"),
                    {"k": kind, "r": rid},
                )
            if replace_scope_ws:
                kind_ws, ws = replace_scope_ws
                sess.execute(text("DELETE FROM rag_documents WHERE kind = :k AND workspace = :ws"), {"k": kind_ws, "ws": ws})
            for it in items:
                tokens = tokenize(f"{it.get('title', '')} {it.get('content', '')}")
                vec = _embed_cached(tokens or it.get("content", ""))
                sess.execute(
                    text(
                        """INSERT INTO rag_documents (doc_key, kind, repo_id, workspace, title, content, fts, embedding, meta, updated_at)
                           VALUES (:k, :kind, :rid, :ws, :t, :c, to_tsvector('simple', :tok), CAST(:e AS vector), CAST(:meta AS jsonb), now())
                           ON CONFLICT (doc_key) DO UPDATE SET
                             kind = EXCLUDED.kind, repo_id = EXCLUDED.repo_id, workspace = EXCLUDED.workspace,
                             title = EXCLUDED.title, content = EXCLUDED.content, fts = EXCLUDED.fts,
                             embedding = EXCLUDED.embedding, meta = EXCLUDED.meta, updated_at = now()"""
                    ),
                    {
                        "k": it["doc_key"],
                        "kind": it.get("kind", ""),
                        "rid": int(it.get("repo_id") or 0),
                        "ws": it.get("workspace", "") or "",
                        "t": (it.get("title") or "")[:500],
                        "c": (it.get("content") or "")[:20000],
                        "tok": tokens[:6000],
                        "e": json.dumps(vec),
                        "meta": json.dumps(it.get("meta") or {}, ensure_ascii=False),
                    },
                )
            sess.commit()
            return len(items)
        except Exception as exc:  # noqa: BLE001
            sess.rollback()
            log.warning("bulk index failed (rolled back): %s", exc)
            return 0


def hybrid_search(
    query: str,
    kinds: tuple[str, ...] | list[str] = (),
    limit: int = 8,
    repo_id: int = 0,
    updated_after: "datetime | None" = None,
    workspace: str = "",
) -> list[dict]:
    """混合检索：向量近邻 + 全文 ts_rank 两路召回，RRF 融合。

    updated_after：时间窗口过滤（OpenWiki time_filter 移植——问答里"本周/最近N天"
    转成检索窗口），SQL 两路与 python 兜底均生效。
    workspace 非空 → 严格按工作区过滤（LightRAG 工作区隔离）；否则按 repo_id（0=全部）。
    返回 [{doc_key, kind, title, content, repo_id, score, vec_rank, fts_rank}]。
    """
    tokens = tokenize(query)
    qvec = _embed_cached(tokens or query)
    with get_session() as sess:
        if not _tables_ready(sess):
            return _python_fallback(sess, query, tokens, kinds, limit, repo_id, updated_after, workspace)
        from sqlalchemy import text

        kind_filter = " AND kind = ANY(:kinds)" if kinds else ""
        scope_params: dict[str, object]
        if workspace:
            scope_filter, scope_params = " AND workspace = :ws", {"ws": workspace}
        else:
            scope_filter = " AND repo_id = :rid" if repo_id else ""
            scope_params = {"rid": repo_id} if repo_id else {}
        time_filter = " AND updated_at >= :after" if updated_after else ""
        cand = max(limit * 3, 15)
        params: dict[str, object] = {"e": json.dumps(qvec), "k": cand, **scope_params}
        if kinds:
            params["kinds"] = list(kinds)
        if updated_after:
            params["after"] = updated_after

        vec_rows = sess.execute(
            text(f"SELECT doc_key, kind, title, content, repo_id, meta, embedding <-> CAST(:e AS vector) AS dist FROM rag_documents WHERE 1=1{kind_filter}{scope_filter}{time_filter} ORDER BY dist LIMIT :k"),
            params,
        ).all()
        fts_params = dict(params)
        tq = _fts_query(tokens)
        fts_rows = []
        if tq:
            fts_params["tq"] = tq
            fts_rows = sess.execute(
                text(f"SELECT doc_key, kind, title, content, repo_id, meta, ts_rank(fts, to_tsquery('simple', :tq)) AS rank FROM rag_documents WHERE fts @@ to_tsquery('simple', :tq){kind_filter}{scope_filter}{time_filter} ORDER BY rank DESC LIMIT :k"),
                fts_params,
            ).all()

        if not vec_rows and not fts_rows:
            return _python_fallback(sess, query, tokens, kinds, limit, repo_id, updated_after, workspace)

        rrf: dict[str, float] = {}
        info: dict[str, dict] = {}

        def _meta(raw) -> dict:  # type: ignore[no-untyped-def]
            if isinstance(raw, dict):
                return raw
            try:
                return json.loads(raw or "{}")
            except (json.JSONDecodeError, TypeError):
                return {}

        for rank, r in enumerate(vec_rows):
            rrf[str(r[0])] = rrf.get(str(r[0]), 0.0) + 1.0 / (_RRF_K + rank + 1)
            info[str(r[0])] = {"doc_key": str(r[0]), "kind": str(r[1]), "title": str(r[2]), "content": str(r[3]), "repo_id": int(r[4] or 0), "meta": _meta(r[5]), "vec_rank": rank + 1, "fts_rank": 0}
        for rank, r in enumerate(fts_rows):
            k = str(r[0])
            rrf[k] = rrf.get(k, 0.0) + 1.0 / (_RRF_K + rank + 1)
            if k in info:
                info[k]["fts_rank"] = rank + 1
            else:
                info[k] = {"doc_key": k, "kind": str(r[1]), "title": str(r[2]), "content": str(r[3]), "repo_id": int(r[4] or 0), "meta": _meta(r[5]), "vec_rank": 0, "fts_rank": rank + 1}
        ranked = sorted(rrf.items(), key=lambda x: (-x[1], x[0]))[:limit]
        out = []
        for k, score in ranked:
            item = info[k]
            item["score"] = round(score, 6)
            item["content"] = item["content"][:1500]
            out.append(item)
        return out


def _python_fallback(sess, query: str, tokens: str, kinds, limit: int, repo_id: int, updated_after=None, workspace: str = "") -> list[dict]:  # type: ignore[no-untyped-def]
    """无 pgvector 兜底：python 余弦 + 词面重合。"""
    from sqlalchemy import text

    try:
        rows = sess.execute(text("SELECT doc_key, kind, title, content, repo_id, meta, updated_at, workspace FROM rag_documents")).all()
    except Exception:  # noqa: BLE001
        sess.rollback()
        return []
    qtoks = set(tokens.split())
    qvec = _embed_cached(tokens or query)
    scored = []
    for dk, kind, title, content, rid, meta, uat, ws in rows:
        if kinds and kind not in kinds:
            continue
        if workspace:
            if (ws or "") != workspace:
                continue
        elif repo_id and int(rid or 0) != repo_id:
            continue
        if updated_after is not None and uat is not None and uat < updated_after:
            continue
        dtoks = set(tokenize(f"{title} {content}").split())
        overlap = len(qtoks & dtoks) / (len(qtoks) or 1)
        cvec = _embed_cached(tokenize(f"{title} {content}") or content)
        cos = sum(a * b for a, b in zip(qvec, cvec))
        try:
            m = meta if isinstance(meta, dict) else json.loads(meta or "{}")
        except (json.JSONDecodeError, TypeError):
            m = {}
        scored.append((dk, kind, title, content, rid, m, 0.5 * overlap + 0.5 * cos))
    scored.sort(key=lambda x: -x[6])
    return [
        {"doc_key": dk, "kind": kind, "title": title, "content": content[:1500], "repo_id": int(rid or 0), "meta": m, "score": round(sc, 6), "vec_rank": 0, "fts_rank": 0}
        for dk, kind, title, content, rid, m, sc in scored[:limit]
    ]


# ---------------- 用例检索 ----------------


def index_case(case_code: str, content: str, title: str = "", layer: str = "", category: str = "", repo_id: int = 0) -> None:
    """用例入库时建立混合检索索引（rag_documents 向量+全文）。

    repo_id 必须传真实仓库：检索按 repo 精确过滤，落 0 会导致用例在带仓库的检索里永远不可见。
    """
    index_document(f"case:{case_code}", "case", title or case_code, content, repo_id=repo_id, meta={"layer": layer, "category": category})


def similar_cases(content: str, limit: int = 5, layer: str = "") -> list[dict]:
    """检索 top-k 相似已入库用例（混合检索；元数据从 cases 表回填）。"""
    hits = hybrid_search(content, kinds=("case",), limit=max(limit * 2, 10))
    if hits:
        codes = [h["doc_key"].removeprefix("case:") for h in hits]
        out = [
            {
                "code": h["doc_key"].removeprefix("case:"),
                "title": h["title"],
                "layer": (h.get("meta") or {}).get("layer", ""),
                "category": (h.get("meta") or {}).get("category", ""),
                "target_function": "",
                "score": round(min(1.0, h["score"] * _RRF_K / 2.0), 4),
            }
            for h in hits
        ]
        with get_session() as sess:
            from app.models import Cases

            for c in sess.query(Cases).filter(Cases.code.in_(codes)).all():
                for item in out:
                    if item["code"] == c.code:
                        item.update({"title": c.title, "layer": c.layer, "category": c.category, "target_function": c.target_function})
        if layer:
            filtered = [x for x in out if x["layer"] == layer]
            if filtered:
                out = filtered
        return out[:limit]

    return []
