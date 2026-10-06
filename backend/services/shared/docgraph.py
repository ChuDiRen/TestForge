"""文档级知识图谱（LightRAG 式双层检索）：从非结构化文本（Wiki/需求/缺陷）抽实体+关系。

与 /api/graph 的结构化业务关系图互补：
- 结构化图：repo→模块→函数调用→需求→用例→缺陷（数据库真实关系派生）；
- 文档图：LLM 从文本抽实体/关系，主题级关联（"支付链路的历史缺陷模式"这类
  向量相似度找不到的跨文档语义），local（实体级）+ global（关系/主题级）双层检索。

增量与成本（LightRAG 思路落地）：
- 每份源文档按 content_hash 跳过未变更单元；
- 抽取走 llm_cache（chat_cached, role=extract），同样的输入只付一次 token；
- 选择性删除：按 source_ref 精确重建单文档贡献的实体/关系，不重跑全库。

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

_EXTRACT_SYSTEM = (
    "你是测试平台的知识图谱构建专家。从给定文档中抽取测试域知识实体与关系。"
    "只输出 JSON，结构：{\"entities\":[{\"name\":\"\",\"type\":\"\",\"description\":\"\"}],"
    "\"relations\":[{\"src\":\"\",\"dst\":\"\",\"type\":\"\",\"description\":\"\"}]}。"
    "type 从 module/function/concept/risk/flow/contract 中选；实体名用文档中的原始术语；"
    "关系要体现测试视角（可能暴露/依赖/属于/触发/破坏）。不要输出 JSON 以外的任何内容。"
)


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
    """构建/增量更新文档知识图谱。返回 {docs_total, docs_built, docs_skipped, docs_failed, entities, relations}。"""
    from services.shared.docstatus import mark as mark_doc
    from services.shared.llm_cache import chat_cached

    docs = _collect_sources(repo_id)
    stats = {"docs_total": len(docs), "docs_built": 0, "docs_skipped": 0, "docs_failed": 0, "entities": 0, "relations": 0}
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
            raw = chat_cached(
                f"文档标题：{doc['title']}\n\n文档内容：\n{doc['content'][:3500]}",
                system=_EXTRACT_SYSTEM,
                role="extract",
            )
            obj = _parse_json(raw)
            _apply_doc(repo_id, ref, h, doc, obj)
            stats["docs_built"] += 1
            mark_doc(repo_id, "kg", ref, "ok", detail=f"entities={len(obj.get('entities', []))} relations={len(obj.get('relations', []))}")
        except Exception as exc:  # noqa: BLE001
            stats["docs_failed"] += 1
            mark_doc(repo_id, "kg", ref, "failed", error=str(exc)[:500])
            log.warning("kg build failed %s: %s", ref, exc)
    with get_session() as sess:
        stats["entities"] = int(sess.query(func.count()).select_from(KgEntity).filter(KgEntity.repo_id == repo_id).scalar() or 0)
        stats["relations"] = int(sess.query(func.count()).select_from(KgRelation).filter(KgRelation.repo_id == repo_id).scalar() or 0)
    return stats


def _apply_doc(repo_id: int, source_ref: str, h: str, doc: dict, obj: dict) -> None:
    """单文档抽取结果落库：关系按 source_ref 重建，实体按 (repo,name) 合并 refs。"""
    from services.shared.rag import index_documents_bulk

    ents = [e for e in (obj.get("entities") or []) if isinstance(e, dict) and (e.get("name") or "").strip()]
    rels = [
        r
        for r in (obj.get("relations") or [])
        if isinstance(r, dict) and (r.get("src") or "").strip() and (r.get("dst") or "").strip()
    ]
    names = {(e["name"].strip()) for e in ents}

    with get_session() as sess:
        # 1) 该文档旧关系全删（选择性删除）
        sess.query(KgRelation).filter(KgRelation.repo_id == repo_id, KgRelation.source_ref == source_ref).delete(synchronize_session=False)
        # 2) 实体合并
        for e in ents:
            name = e["name"].strip()
            row = sess.query(KgEntity).filter(KgEntity.repo_id == repo_id, KgEntity.name == name).first()
            if row is None:
                sess.add(
                    KgEntity(
                        repo_id=repo_id,
                        name=name,
                        etype=(e.get("type") or "concept")[:64],
                        description=(e.get("description") or "")[:1500],
                        source_refs=json.dumps([source_ref], ensure_ascii=False),
                        content_hash=h,
                    )
                )
            else:
                try:
                    refs = json.loads(row.source_refs or "[]")
                except json.JSONDecodeError:
                    refs = []
                if source_ref not in refs:
                    refs.append(source_ref)
                row.source_refs = json.dumps(refs, ensure_ascii=False)
                row.content_hash = h
                if not row.description and e.get("description"):
                    row.description = e["description"][:1500]
        # 3) 只被本文档引用且新抽取中消失的实体 → 删除
        for row in sess.query(KgEntity).filter(KgEntity.repo_id == repo_id).all():
            try:
                refs = json.loads(row.source_refs or "[]")
            except json.JSONDecodeError:
                refs = []
            if refs == [source_ref] and row.name not in names:
                sess.delete(row)
        # 4) 关系插入
        for r in rels:
            sess.add(
                KgRelation(
                    repo_id=repo_id,
                    src_name=r["src"].strip()[:250],
                    dst_name=r["dst"].strip()[:250],
                    rtype=(r.get("type") or "related")[:64],
                    description=(r.get("description") or "")[:1000],
                    weight=1.0,
                    source_ref=source_ref,
                )
            )
        sess.commit()

    # 5) 实体/关系入统一检索表（kg_query 直接吃 hybrid_search 结果）；两类各自单事务替换
    with get_session() as sess:
        rel_rows = sess.query(KgRelation).filter(KgRelation.repo_id == repo_id).all()
        degree: dict[str, int] = {}
        for r in rel_rows:
            degree[r.src_name] = degree.get(r.src_name, 0) + 1
            degree[r.dst_name] = degree.get(r.dst_name, 0) + 1
        ent_rows = sess.query(KgEntity).filter(KgEntity.repo_id == repo_id).all()

    def _ent_item(e: KgEntity) -> dict:
        refs = _refs_of(e)
        return {
            "doc_key": f"kg_entity:{repo_id}:{e.name}",
            "kind": "kg_entity",
            "repo_id": repo_id,
            "title": e.name,
            "content": f"{e.name}（{e.etype}）：{e.description}",
            "meta": {"etype": e.etype, "degree": degree.get(e.name, 0), "source_refs": refs},
        }

    def _rel_item(r: KgRelation) -> dict:
        return {
            "doc_key": f"kg_relation:{repo_id}:{r.id}",
            "kind": "kg_relation",
            "repo_id": repo_id,
            "title": f"{r.src_name} -{r.rtype}-> {r.dst_name}",
            "content": f"{r.src_name} -{r.rtype}-> {r.dst_name}：{r.description}",
            "meta": {"rtype": r.rtype, "src": r.src_name, "dst": r.dst_name},
        }

    index_documents_bulk([_ent_item(e) for e in ent_rows], replace_scope=("kg_entity", repo_id))
    index_documents_bulk([_rel_item(r) for r in rel_rows], replace_scope=("kg_relation", repo_id))


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
    """双层检索（LightRAG 查询模式裁剪）：local 实体级 / global 主题级 / mix 融合。"""
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
