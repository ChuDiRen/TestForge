"""六模式 KG 查询引擎（LightRAG 全量对齐）：naive / local / global / hybrid / mix + rerank + 流式。

六步管线（论文 §查询）：
1. 双层关键词：high-level（主题）+ low-level（实体）分头抽取；
2. 双层召回：local → kg_entity 向量库（low keywords）；global → kg_relation + kg_community
   向量库（high keywords）；naive → kg_chunk 原文库；hybrid = local+global；mix = hybrid+naive（默认）；
3. token 预算：实体/关系/chunk 各自预算内截断（config 可调）；
4. source 反查回填：实体/关系的 source_refs 反查 chunk/源文档 → references 引用清单；
5. rerank（可选配置）；
6. 生成：role=query 流式回答（contexts 一并返回供 RAGAS）；查询级答案缓存（mode+query 键控）。

Key 未配置：检索/contexts 永不失败；生成路径显式给出配置指引（无假实现原则）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Iterator

from sqlalchemy import text

from app.core.tokenizer import count_tokens, truncate_tokens
from app.db.session import get_session
from app.models import KgRelation

log = logging.getLogger("shared.kg_query")

MODES = ("naive", "local", "global", "hybrid", "mix")


def _budgets() -> dict[str, int]:
    from app.core.config import get_settings

    s = get_settings()
    return {
        "entity": s.kg_entity_token_max,
        "relation": s.kg_relation_token_max,
        "chunk": s.kg_chunk_token_max,
    }


def _rrf_merge(result_sets: list[list[dict]], limit: int) -> list[dict]:
    """多路召回 RRF 融合（k=60，与 rag.hybrid_search 同参）。"""
    k = 60.0
    scores: dict[str, float] = {}
    info: dict[str, dict] = {}
    for rs in result_sets:
        for rank, hit in enumerate(rs):
            dk = hit["doc_key"]
            scores[dk] = scores.get(dk, 0.0) + 1.0 / (k + rank + 1)
            if dk not in info:
                info[dk] = {**hit, "score": 0.0}
    ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0]))[:limit]
    out = []
    for dk, sc in ranked:
        item = info[dk]
        item["score"] = round(sc, 6)
        out.append(item)
    return out


def _maybe_rerank(query: str, hits: list[dict], limit: int) -> list[dict]:
    """rerank 已配置时对候选重排；未配置/失败保持 RRF 序。"""
    from app.services.knowledge import rerank as rr

    if not hits or not rr.enabled():
        return hits[:limit]
    docs = [f"{h['title']}\n{h['content'][:800]}" for h in hits]
    ranked = rr.rerank(query, docs, top_n=limit)
    out = []
    for idx, score in ranked:
        if 0 <= idx < len(hits):
            item = {**hits[idx], "rerank_score": round(score, 4)}
            out.append(item)
    return out[:limit]


def _hop_relations(repo_id: int, workspace: str, names: list[str], limit: int) -> list[dict]:
    """local 扩展：命中实体的一跳关系（图导航，向量检索给不了）。"""
    with get_session() as sess:
        q = sess.query(KgRelation).filter(
            KgRelation.src_name.in_(names) | KgRelation.dst_name.in_(names)
        )
        if workspace:
            q = q.filter(KgRelation.workspace == workspace)
        else:
            q = q.filter(KgRelation.repo_id == repo_id, KgRelation.workspace == "")
        rows = q.order_by(KgRelation.weight.desc()).limit(limit * 2).all()
    return [
        {
            "doc_key": f"kg_relation:{r.repo_id}:{r.workspace}:{r.id}",
            "kind": "kg_relation",
            "title": f"{r.src_name} -{r.rtype}-> {r.dst_name}",
            "content": f"{r.src_name} -{r.rtype}-> {r.dst_name}：{r.description}",
            "repo_id": r.repo_id,
            "meta": {"rtype": r.rtype, "src": r.src_name, "dst": r.dst_name, "weight": r.weight},
            "score": 0.0,
        }
        for r in rows
    ]


def _backfill_references(ent_hits: list[dict], rel_hits: list[dict], chunk_hits: list[dict]) -> list[dict]:
    """source_refs/source_ref 反查 → 引用清单（chunk 命中带原文片段，实体/关系带来源标签）。"""
    refs: dict[str, dict] = {}

    def _add(ref: str, via: str) -> None:
        if not ref or ref in refs:
            return
        refs[ref] = {"ref": ref, "via": via}

    for e in ent_hits:
        for r in (e.get("meta") or {}).get("source_refs", []) or []:
            _add(r, "entity:" + e["title"])
    for r in rel_hits:
        for rr_ in (r.get("meta") or {}).get("source_refs", []) or []:
            _add(rr_, "relation")
        legacy = (r.get("meta") or {}).get("source_ref", "")
        if legacy:
            _add(legacy, "relation")
    out = sorted(refs.values(), key=lambda x: x["ref"])
    for c in chunk_hits:
        out.append(
            {
                "ref": c["doc_key"],
                "via": "chunk",
                "title": c["title"],
                "snippet": c["content"][:200],
                "parent": (c.get("meta") or {}).get("parent", ""),
            }
        )
    return out


def kg_search(
    query: str,
    mode: str = "mix",
    repo_id: int = 0,
    workspace: str = "",
    top_k: int = 8,
    websearch: bool = False,
) -> dict:
    """六模式检索（无 LLM 生成，纯召回 + contexts；生成走 answer_stream / build_answer）。

    返回 {mode, keywords, entities, relations, chunks, communities, contexts, references, web_results, timings}。
    """
    t0 = time.time()
    if mode not in MODES:
        mode = "mix"
    from app.services.knowledge.kg_keywords import dual_keywords

    kw = dual_keywords(query)
    b = _budgets()
    ent_hits: list[dict] = []
    rel_hits: list[dict] = []
    chunk_hits: list[dict] = []

    from app.services.knowledge.rag import hybrid_search

    want_entities = mode in ("local", "hybrid", "mix")
    want_relations = mode in ("global", "hybrid", "mix")
    want_chunks = mode in ("naive", "mix")

    if want_entities:
        ent_hits = _maybe_rerank(
            query, hybrid_search(kw["low_level"] or query, kinds=("kg_entity",), limit=top_k, repo_id=repo_id, workspace=workspace), top_k
        )
    if want_relations:
        rel_hits = _maybe_rerank(
            query,
            _rrf_merge(
                [
                    hybrid_search(kw["high_level"] or query, kinds=("kg_relation",), limit=top_k, repo_id=repo_id, workspace=workspace),
                    hybrid_search(query, kinds=("kg_community",), limit=max(top_k // 2, 2), repo_id=repo_id, workspace=workspace),
                ],
                top_k,
            ),
            top_k,
        )
        # community 报告与关系分列（报告也属于 global 语义层）
    if want_chunks:
        chunk_hits = _maybe_rerank(
            query, hybrid_search(query, kinds=("kg_chunk",), limit=top_k, repo_id=repo_id, workspace=workspace), top_k
        )

    # local 1-hop 图扩展（local/hybrid/mix 且有实体命中）
    if want_entities and ent_hits:
        hop = _hop_relations(repo_id, workspace, [e["title"] for e in ent_hits], top_k)
        have = {(r["meta"].get("src"), r["meta"].get("dst"), r["meta"].get("rtype")) for r in rel_hits}
        rel_hits += [h for h in hop if (h["meta"].get("src"), h["meta"].get("dst"), h["meta"].get("rtype")) not in have][: top_k * 2]

    communities = [h for h in rel_hits if (h.get("meta") or {}).get("community")]
    pure_relations = [h for h in rel_hits if not (h.get("meta") or {}).get("community")]

    # token 预算截断
    ent_budget, rel_budget, chunk_budget = b["entity"], b["relation"], b["chunk"]

    def _pack(hits: list[dict], budget: int, field: str = "content") -> tuple[list[dict], int]:
        out: list[dict] = []
        used = 0
        for h in hits:
            t = count_tokens(str(h.get(field, "")))
            if used + t > budget and out:
                break
            out.append(h)
            used += t
        return out, used

    ent_hits, ent_used = _pack(ent_hits, ent_budget)
    pure_relations, rel_used = _pack(pure_relations, rel_budget)
    chunk_hits, chunk_used = _pack(chunk_hits, chunk_budget)

    references = _backfill_references(ent_hits, pure_relations, chunk_hits)

    web_results: list[dict] = []
    if websearch and not (ent_hits or pure_relations or chunk_hits):
        from app.core.config import get_settings
        from app.core.websearch import search

        s = get_settings()
        web_results = search(query, max_results=s.websearch_max_results)
        for w in web_results:
            references.append({"ref": w["url"], "via": "websearch", "title": w["title"], "snippet": w["snippet"]})

    contexts = {
        "entities": "\n".join(_render_entity(e) for e in ent_hits),
        "relations": "\n".join(_render_relation(r) for r in pure_relations),
        "chunks": "\n\n".join(f"[{c['title']}]\n{truncate_tokens(c['content'], 800)}" for c in chunk_hits),
        "communities": "\n".join(_render_relation(c) for c in communities),
    }
    return {
        "mode": mode,
        "keywords": kw,
        "entities": [_trim(e) for e in ent_hits],
        "relations": [_trim(r) for r in pure_relations],
        "communities": [_trim(c) for c in communities],
        "chunks": [_trim(c) for c in chunk_hits],
        "contexts": contexts,
        "references": references[:50],
        "web_results": web_results,
        "tokens_used": {"entity": ent_used, "relation": rel_used, "chunk": chunk_used},
        "timings": {"total_ms": int((time.time() - t0) * 1000)},
    }


def _render_entity(e: dict) -> str:
    meta = e.get("meta") or {}
    body = e["content"].split("：", 1)[-1]
    return f"- **{e['title']}**（{meta.get('etype', 'concept')}）：{body}"


def _render_relation(r: dict) -> str:
    body = (r.get("content") or "").split("：", 1)[-1]
    return f"- {r['title']}：{body}"


def _trim(h: dict) -> dict:
    return {"name": h["title"], "content": h["content"][:400], "score": h.get("score", 0.0), "meta": h.get("meta", {}), "doc_key": h["doc_key"]}


def _cache_key(query: str, mode: str, workspace: str) -> str:
    from app.services.knowledge.llm import model_for

    raw = f"answer|{mode}|{workspace}|{model_for('query')}|{query}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _answer_cached(query: str, mode: str, workspace: str) -> str | None:
    from app.models import LlmCache

    with get_session() as sess:
        row = sess.get(LlmCache, _cache_key(query, mode, workspace))
        return row.response if row is not None else None


def _answer_cache_put(query: str, mode: str, workspace: str, answer: str) -> None:
    from app.models import LlmCache

    with get_session() as sess:
        sess.merge(
            LlmCache(
                cache_key=_cache_key(query, mode, workspace),
                role="query",
                model="kg-answer",
                prompt=f"{mode}|{query}"[:2000],
                response=answer,
            )
        )
        sess.commit()


def clear_query_cache() -> int:
    from app.models import LlmCache

    with get_session() as sess:
        n = sess.query(LlmCache).filter(LlmCache.role == "query", LlmCache.model == "kg-answer").delete(synchronize_session=False)
        sess.commit()
        return int(n or 0)


def _render_context(res: dict, prefix: str) -> str:
    parts = [prefix] if prefix else []
    if res["contexts"]["entities"]:
        parts.append("### 知识实体\n" + res["contexts"]["entities"])
    if res["contexts"]["relations"]:
        parts.append("### 知识关系\n" + res["contexts"]["relations"])
    if res["contexts"]["communities"]:
        parts.append("### 社区报告\n" + res["contexts"]["communities"])
    if res["contexts"]["chunks"]:
        parts.append("### 原文片段\n" + res["contexts"]["chunks"])
    if res["web_results"]:
        parts.append("### Web 检索兜底\n" + "\n".join(f"- [{w['title']}]({w['url']})：{w['snippet']}" for w in res["web_results"]))
    return "\n\n".join(parts)


_ANSWER_SYSTEM = (
    "你是测试平台知识助手。基于给定的知识上下文（实体/关系/社区报告/原文片段）回答用户问题。"
    "规则：只用给定上下文中的信息；引用来源时标注 [来源: 实体名/文档标题]；上下文不够时明确说明"
    "「当前知识库中未检索到足够信息」，不要编造。用中文回答。"
)


def answer_stream(query: str, mode: str = "mix", repo_id: int = 0, workspace: str = "", top_k: int = 8, websearch: bool = False, use_cache: bool = True) -> Iterator[dict]:
    """流式回答：先吐检索事件（type=retrieved），再逐段吐回答（type=delta），最后 done（完整结果）。

    查询缓存命中时 delta 直接回放缓存全文；LLM 未配置时给 notice 事件（检索结果照常返回）。
    """
    from app.core.config import get_settings

    res = kg_search(query, mode=mode, repo_id=repo_id, workspace=workspace, top_k=top_k, websearch=websearch)
    yield {"type": "retrieved", "payload": {k: v for k, v in res.items() if k != "contexts"}}

    cached = _answer_cached(query, mode, workspace) if (use_cache and get_settings().query_cache_enabled) else None
    if cached:
        yield {"type": "delta", "delta": cached, "cached": True}
        res["answer"] = cached
        res["cached"] = True
        yield {"type": "done", "payload": res}
        return

    s = get_settings()
    prefix = s.user_prompt_prefix
    if not s.llm_api_key:
        yield {
            "type": "notice",
            "message": "LLM_API_KEY 未配置：已返回检索结果（contexts/references），配置后可获得生成式回答",
        }
        res["answer"] = ""
        yield {"type": "done", "payload": res}
        return

    from app.services.knowledge.llm import chat_stream

    prompt = f"## 用户问题\n{query}\n\n## 知识上下文\n{_render_context(res, prefix)}"
    pieces: list[str] = []
    try:
        for delta in chat_stream(prompt, system=_ANSWER_SYSTEM, role="query"):
            pieces.append(delta)
            yield {"type": "delta", "delta": delta}
    except Exception as exc:  # noqa: BLE001
        yield {"type": "notice", "message": f"生成失败（检索结果有效）：{exc}"}
    answer = "".join(pieces)
    if answer and use_cache and s.query_cache_enabled:
        try:
            _answer_cache_put(query, mode, workspace, answer)
        except Exception as exc:  # noqa: BLE001
            log.debug("answer cache write failed: %s", exc)
    res["answer"] = answer
    res["cached"] = False
    yield {"type": "done", "payload": res}


def kg_graph(repo_id: int = 0, workspace: str = "", max_nodes: int = 500, search: str = "", focus: str = "", depth: int = 1) -> dict:
    """文档 KG 图数据（图谱查看器用）：节点=实体，边=关系；focus+depth 支持 BFS 子图。"""
    with get_session() as sess:
        if workspace:
            w = "workspace = :ws"
            params: dict = {"ws": workspace}
        else:
            w = "repo_id = :rid AND workspace = ''"
            params = {"rid": repo_id}
        name_filter = ""
        if search:
            name_filter = " AND name ILIKE :q"
            params["q"] = f"%{search}%"
        if focus:
            # BFS 子图：focus 实体 N 跳内节点
            rows = sess.execute(
                text(
                    f"""WITH RECURSIVE walk(name, d) AS (
                        SELECT CAST(:focus AS varchar), 0
                        UNION
                        SELECT CASE WHEN k.src_name = walk.name THEN k.dst_name ELSE k.src_name END, walk.d + 1
                        FROM walk JOIN kg_relations k ON (k.src_name = walk.name OR k.dst_name = walk.name)
                          AND k.{w} AND walk.d < :depth
                    )
                    SELECT DISTINCT name FROM walk"""
                ),
                {**params, "focus": focus, "depth": max(1, min(depth, 4))},
            ).all()
            names = {r[0] for r in rows}
            if not names:
                return {"nodes": [], "edges": [], "focus": focus}
            name_list = sorted(names)
            ent_rows = sess.execute(
                text(f"SELECT name, etype, description, source_refs FROM kg_entities WHERE {w} AND name = ANY(:names)"),
                {**params, "names": name_list},
            ).all()
            rel_rows = sess.execute(
                text(f"SELECT id, src_name, dst_name, rtype, weight FROM kg_relations WHERE {w} AND (src_name = ANY(:names) AND dst_name = ANY(:names))"),
                {**params, "names": name_list},
            ).all()
        else:
            ent_rows = sess.execute(
                text(f"SELECT name, etype, description, source_refs FROM kg_entities WHERE {w}{name_filter} LIMIT :n"),
                {**params, "n": max_nodes},
            ).all()
            name_list = [r[0] for r in ent_rows]
            rel_rows = sess.execute(
                text(f"SELECT id, src_name, dst_name, rtype, weight FROM kg_relations WHERE {w} AND src_name = ANY(:names) AND dst_name = ANY(:names)"),
                {**params, "names": name_list or [""]},
            ).all()
    nodes = [
        {"id": r[0], "label": r[0], "etype": r[1], "degree": 0, "sources": len(json.loads(r[3] or "[]")) if isinstance(r[3], str) else 0}
        for r in ent_rows
    ]
    edges = [{"id": r[0], "source": r[1], "target": r[2], "rtype": r[3], "weight": float(r[4] or 1.0)} for r in rel_rows]
    deg: dict[str, int] = {}
    for e in edges:
        deg[e["source"]] = deg.get(e["source"], 0) + 1
        deg[e["target"]] = deg.get(e["target"], 0) + 1
    for n in nodes:
        n["degree"] = deg.get(n["id"], 0)
    nodes.sort(key=lambda n: -n["degree"])
    return {"nodes": nodes[:max_nodes], "edges": edges, "focus": focus or None}


def entity_exists(name: str, repo_id: int = 0, workspace: str = "") -> dict:
    from app.models import KgEntity

    with get_session() as sess:
        q = sess.query(KgEntity).filter(KgEntity.name == name)
        q = q.filter(KgEntity.workspace == workspace) if workspace else q.filter(KgEntity.repo_id == repo_id, KgEntity.workspace == "")
        row = q.first()
    return {"exists": row is not None, "entity": {"name": row.name, "etype": row.etype, "description": row.description} if row else None}


def list_chunks(parent_doc_key: str, workspace: str = "") -> list[dict]:
    """某主文档的 chunk 清单（文档管理「查看分块」用）。"""
    from app.services.knowledge.rag import _tables_ready

    out: list[dict] = []
    with get_session() as sess:
        if not _tables_ready(sess):
            return out
        rows = sess.execute(
            text("SELECT doc_key, title, LENGTH(content) AS chars, meta, updated_at FROM rag_documents WHERE kind = 'kg_chunk' AND meta->>'parent' = :p ORDER BY meta->>'index'"),
            {"p": parent_doc_key},
        ).all()
    for r in rows:
        meta = r[3] if isinstance(r[3], dict) else {}
        out.append({"doc_key": r[0], "title": r[1], "chars": int(r[2] or 0), "index": meta.get("index", 0), "strategy": meta.get("strategy", ""), "updated_at": r[4].isoformat() if r[4] else None})
    return out
