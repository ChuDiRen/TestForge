"""文档级知识图谱：从非结构化文本（Wiki/需求/缺陷）抽实体+关系，v2 全量内核。

与 /api/graph 的结构化业务关系图互补：
- 结构化图：repo→模块→函数调用→需求→用例→缺陷（数据库真实关系派生）；
- 文档图：LLM 从文本抽实体/关系，主题级关联（"支付链路的历史缺陷模式"这类
  向量相似度找不到的跨文档语义）。

v2 内核（LightRAG 全量对齐，2026-10）：
- 分块：chunking 四策略（默认 paragraph），每 chunk 独立抽取；
- 抽取：逐 chunk LLM + gleaning 多轮补抽，llm_cache 键控（未变 chunk 零成本）；
- 合并：kg_merge 三阶段（type 投票 / 描述源 <8 拼接 ≥8 摘要 / 关系 weight 证据计数）；
- 删除：kg_extractions 留存每文档抽取，撤文档从剩余抽取重建（不重跑 LLM）；
- 检索：kg_query.kg_search 六模式（naive/local/global/hybrid/mix）。
本模块保留 build_from_repo（repo 语料批量构建入口）与 kg_query（legacy 兼容层，MCP/助手在用）。

LLM Key 未配置时：build 显式报错（无 mock 原则）；query 走确定性关键词检索不受影响。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re

from sqlalchemy import func

from services.shared.db import get_session
from services.shared.models import Defects, KgEntity, KgRelation, Requirements, WikiPages

log = logging.getLogger("shared.docgraph")


def _doc_hash(title: str, content: str) -> str:
    return hashlib.md5(f"{title}\n{content}".encode("utf-8")).hexdigest()


def _parse_json(raw: str) -> dict:
    """容错解析 LLM 输出：剥代码围栏、截首个 { 到末个 }。"""
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        return {}


def _collect_sources(repo_id: int) -> list[dict]:
    """知识源文档清单：wiki 模块/仓库页（函数页太多且低密度）+ 缺陷 + 需求。"""
    docs: list[dict] = []
    with get_session() as sess:
        pages = sess.query(WikiPages).filter(WikiPages.repo_id == repo_id, WikiPages.level.in_(("module", "repo", "system"))).all()
        for p in pages:
            docs.append({"source_ref": f"wiki:{p.id}", "kind": "wiki", "title": p.title, "content": p.content_md})
        for d in sess.query(Defects).limit(500).all():
            docs.append(
                {
                    "source_ref": f"defect:{d.code}",
                    "kind": "defect",
                    "title": f"{d.code} {d.title}",
                    "content": f"严重度 {d.severity} 状态 {d.status}\n{(d.detail or '')[:2000]}\n建议: {(d.suggestion or '')[:1500]}",
                }
            )
        for r in sess.query(Requirements).limit(200).all():
            docs.append({"source_ref": f"req:{r.code}", "kind": "requirement", "title": f"{r.code} {r.title}", "content": (r.body or "")[:4000]})
    return docs


def build_from_repo(repo_id: int, trace_id: str = "") -> dict:
    """构建/增量更新文档知识图谱（v2 内核：分块→抽取+gleaning→三阶段合并）。

    返回 {docs_total, docs_built, docs_skipped, docs_failed, entities, relations}。
    """
    from services.shared.doc_pipeline import settings_get
    from services.shared.docstatus import mark as mark_doc
    from services.shared.kg_extract import extract_document
    from services.shared.kg_merge import apply_extraction

    docs = _collect_sources(repo_id)
    stats = {"docs_total": len(docs), "docs_built": 0, "docs_skipped": 0, "docs_failed": 0, "entities": 0, "relations": 0}
    strategy = settings_get("chunk_strategy", "paragraph")
    chunk_size = int(settings_get("chunk_size", "1200"))
    gleaning = int(settings_get("gleaning_rounds", "1"))
    drop_refs = settings_get("chunk_drop_references", "True").lower() == "true"
    for doc in docs:
        ref = doc["source_ref"]
        h = _doc_hash(doc["title"], doc["content"])
        with get_session() as sess:
            # 该文档是否已按同一内容抽过（ref 命中且 hash 相同 → 跳过，增量零成本）
            row = (
                sess.query(KgEntity)
                .filter(KgEntity.repo_id == repo_id, KgEntity.source_refs.contains(f'"{ref}"'), KgEntity.content_hash == h)
                .first()
            )
        if row is not None:
            stats["docs_skipped"] += 1
            continue
        try:
            from services.shared.chunking import chunk_text

            chunks = chunk_text(doc["content"], strategy=strategy, chunk_size=chunk_size, drop_references=drop_refs)
            extraction = extract_document(chunks, doc["title"], gleaning=gleaning)
            apply_extraction(
                repo_id,
                workspace="",
                source_ref=ref,
                doc_title=doc["title"],
                extraction=extraction,
                content_hash=h,
                chunk_count=len(chunks),
                gleaning_rounds=extraction.get("gleaning_rounds", 0),
            )
            stats["docs_built"] += 1
            mark_doc(repo_id, "kg", ref, "ok", detail=f"chunks={len(chunks)} entities={len(extraction.get('entities', []))} relations={len(extraction.get('relations', []))}")
        except Exception as exc:  # noqa: BLE001
            stats["docs_failed"] += 1
            mark_doc(repo_id, "kg", ref, "failed", error=str(exc)[:500])
            log.warning("kg build failed %s: %s", ref, exc)
    stats["entities"], stats["relations"] = kg_stats(repo_id)["entities"], kg_stats(repo_id)["relations"]
    return stats


def _refs_of(e: KgEntity) -> list[str]:
    try:
        v = json.loads(e.source_refs or "[]")
        return v if isinstance(v, list) else []
    except json.JSONDecodeError:
        return []


def _keywords(question: str) -> str:
    """检索词：LLM keyword 角色优先，Key 缺失回退确定性 token（query 永不因无 Key 失败）。"""
    try:
        from services.shared.llm_cache import chat_cached

        raw = chat_cached(
            f"从问题中提取 3~6 个检索关键词（英文小写/中文原词，空格分隔，直接输出不要解释）：\n{question}",
            role="keyword",
        )
        kw = re.sub(r"[^\w\u4e00-\u9fff\s]", " ", raw).strip()
        if kw:
            return kw
    except Exception:  # noqa: BLE001
        pass
    from services.shared.rag import tokenize

    return tokenize(question)


def kg_query(repo_id: int, question: str, mode: str = "mix", limit: int = 6) -> dict:
    """legacy 双层检索（local 实体级 / global 主题级 / mix 融合）。

    v2 六模式走 services.shared.kg_query.kg_search（/api/kg/search）；
    本函数保持响应形状不变（MCP knowledge_query / AI 助手 explore 工具在用）。
    """
    from services.shared.rag import hybrid_search

    kw = _keywords(question)
    entities: list[dict] = []
    relations: list[dict] = []
    if mode in ("local", "mix"):
        entities = hybrid_search(kw or question, kinds=("kg_entity",), limit=limit, repo_id=repo_id)
    if mode in ("global", "mix"):
        relations = hybrid_search(kw or question, kinds=("kg_relation",), limit=limit, repo_id=repo_id)

    # local 扩展：命中实体的 1-hop 关系补入（图导航，向量检索给不了）
    if entities:
        hit_names = [e["title"] for e in entities]
        with get_session() as sess:
            hop = (
                sess.query(KgRelation)
                .filter(KgRelation.repo_id == repo_id, KgRelation.src_name.in_(hit_names) | KgRelation.dst_name.in_(hit_names))
                .limit(limit * 2)
                .all()
            )
        have = {(r["meta"].get("src"), r["meta"].get("dst"), r["meta"].get("rtype")) for r in relations}
        for r in hop:
            key = (r.src_name, r.dst_name, r.rtype)
            if key not in have:
                relations.append(
                    {
                        "doc_key": f"kg_relation:{repo_id}:{r.id}",
                        "kind": "kg_relation",
                        "title": f"{r.src_name} -{r.rtype}-> {r.dst_name}",
                        "content": f"{r.src_name} -{r.rtype}-> {r.dst_name}：{r.description}",
                        "repo_id": repo_id,
                        "meta": {"rtype": r.rtype, "src": r.src_name, "dst": r.dst_name},
                        "score": 0.0,
                    }
                )

    return {
        "mode": mode,
        "keywords": kw,
        "entities": [_trim_hit(e) for e in entities],
        "relations": [_trim_hit(r) for r in relations[: limit * 2]],
        "rendered": _render(entities, relations[: limit * 2]),
    }


def _trim_hit(h: dict) -> dict:
    return {"name": h["title"], "content": h["content"][:400], "score": h.get("score", 0.0), "meta": h.get("meta", {})}


def _render(entities: list[dict], relations: list[dict]) -> str:
    lines = []
    if entities:
        lines.append("### 相关知识实体（文档图谱）")
        for e in entities:
            meta = e.get("meta") or {}
            lines.append(f"- **{e['title']}**（{meta.get('etype', 'concept')}）：{e['content'].split('：', 1)[-1][:200]}")
    if relations:
        lines.append("")
        lines.append("### 相关知识关系")
        for r in relations:
            lines.append(f"- {r['title']}：{(r.get('content') or '').split('：', 1)[-1][:200]}")
    return "\n".join(lines)


def kg_stats(repo_id: int = 0) -> dict:
    with get_session() as sess:
        eq = sess.query(func.count()).select_from(KgEntity)
        rq = sess.query(func.count()).select_from(KgRelation)
        if repo_id:
            eq = eq.filter(KgEntity.repo_id == repo_id)
            rq = rq.filter(KgRelation.repo_id == repo_id)
        return {"entities": int(eq.scalar() or 0), "relations": int(rq.scalar() or 0)}
