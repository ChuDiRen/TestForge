"""文档级知识图谱：从非结构化文本（Wiki/需求/缺陷）抽实体+关系的查询与源枚举层。

与 /api/graph 的结构化业务关系图互补：
- 结构化图：repo→模块→函数调用→需求→用例→缺陷（数据库真实关系派生）；
- 文档图：LLM 从文本抽实体/关系，主题级关联（"支付链路的历史缺陷模式"这类
  向量相似度找不到的跨文档语义）。

构建引擎唯一化（2026-10 合并）：分块/抽取/合并/删除全部在 doc_pipeline，
本模块只负责源枚举（build_from_repo：wiki 模块/仓库页+需求 → submit_document
入队；缺陷只走 rag_documents 检索语料，不进图谱抽取）与查询（kg_query 确定性双层检索，
MCP/助手在用）。

检索内核（LightRAG 全量对齐）：
- 抽取：逐 chunk LLM + gleaning 多轮补抽，llm_cache 键控（未变 chunk 零成本）；
- 合并：kg_merge 三阶段（type 投票 / 描述源 <8 拼接 ≥8 摘要 / 关系 weight 证据计数）；
- 删除：kg_extractions 留存每文档抽取，撤文档从剩余抽取重建（不重跑 LLM）；
- 检索：kg_query.kg_search 六模式（naive/local/global/hybrid/mix）。

LLM Key 未配置时：build 逐源 failed 落状态（无 mock 原则）；query 走确定性关键词检索不受影响。
"""

from __future__ import annotations

import json
import logging
import re

from sqlalchemy import func

from app.db.session import get_session
from app.models import KgEntity, KgRelation, Requirements, WikiPages

log = logging.getLogger("shared.docgraph")


def _collect_sources(repo_id: int) -> list[dict]:
    """知识源文档清单：wiki 模块/仓库页（函数页太多且低密度）+ 需求。

    缺陷不入图谱抽取（2026-10 调整）：实体密度低、消费路径是检索（生成侧
    hybrid_search kind=defect 找相似缺陷），建缺陷时已入 rag_documents——
    再走 KG 抽取纯属烧 LLM。
    """
    docs: list[dict] = []
    with get_session() as sess:
        pages = sess.query(WikiPages).filter(WikiPages.repo_id == repo_id, WikiPages.level.in_(("module", "repo", "system"))).all()
        for p in pages:
            docs.append({"source_ref": f"wiki:{p.id}", "kind": "wiki", "title": p.title, "content": p.content_md})
        for r in sess.query(Requirements).limit(200).all():
            docs.append({"source_ref": f"req:{r.code}", "kind": "requirement", "title": f"{r.code} {r.title}", "content": (r.body or "")[:4000]})
    return docs


def build_from_repo(repo_id: int, trace_id: str = "") -> dict:
    """构建/增量更新文档知识图谱（v3：源枚举 → 统一异步管线）。

    wiki 模块/仓库页 + 需求经 doc_pipeline.submit_document 入队（priority=10 垫底，
    手动上传永远插队在前），分块/抽取/合并/删除由 doc_pipeline 唯一引擎负责；
    内容未变的源 hash 短路零成本。本函数不再自带构建循环。
    返回 {docs_total, docs_queued, docs_skipped, docs_failed}。
    """
    from app.services.knowledge.doc_pipeline import purge_auto_orphans, submit_document

    docs = _collect_sources(repo_id)
    stats = {"docs_total": len(docs), "docs_queued": 0, "docs_skipped": 0, "docs_failed": 0}
    workspace = f"repo:{repo_id}"
    live_keys: set[str] = set()
    for doc in docs:
        try:
            res = submit_document(
                workspace,
                doc["title"],
                doc["content"],
                repo_id=repo_id,
                filename=doc["source_ref"],
                source=f"auto:{doc['kind']}",
                priority=10,
            )
            live_keys.add(res["doc_key"])
            if res.get("skipped"):
                stats["docs_skipped"] += 1
            else:
                stats["docs_queued"] += 1
        except Exception as exc:  # noqa: BLE001
            stats["docs_failed"] += 1
            log.warning("kg submit failed %s: %s", doc["source_ref"], exc)
    # 源已消失的自动文档（wiki 页 GC / 缺陷需求删除）连带撤图谱贡献
    stats["docs_orphaned"] = purge_auto_orphans(workspace, live_keys)
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
        from app.services.knowledge.llm_cache import chat_cached

        raw = chat_cached(
            f"从问题中提取 3~6 个检索关键词（英文小写/中文原词，空格分隔，直接输出不要解释）：\n{question}",
            role="keyword",
        )
        kw = re.sub(r"[^\w\u4e00-\u9fff\s]", " ", raw).strip()
        if kw:
            return kw
    except Exception:  # noqa: BLE001
        pass
    from app.services.knowledge.rag import tokenize

    return tokenize(question)


def kg_query(repo_id: int, question: str, mode: str = "mix", limit: int = 6) -> dict:
    """双层检索（local 实体级 / global 主题级 / mix 融合）。

    v2 六模式走 app.services.knowledge.kg_query.kg_search（/api/kg/search）；
    本函数保持响应形状不变（MCP knowledge_query / AI 助手 explore 工具在用）。
    """
    from app.services.knowledge.rag import hybrid_search

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
