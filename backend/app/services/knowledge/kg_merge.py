"""实体/关系合并（LightRAG 三阶段合并对齐）：

- type 投票：etype_votes 计数，多数票胜出（同实体跨文档类型冲突的民主裁决）；
- description：desc_sources 按 source_ref 留存各文档描述，<8 条直接拼接、≥8 条 LLM 摘要
  （缓存于 llm_cache，对齐 LightRAG merge_never_disable/summary_max_tokens 语义）；
- 关系 weight = 证据计数（支持该 (src,dst,rtype) 边的文档数），source_refs 留全部证据。

删除语义（LightRAG delete_by_doc → 缓存重建）：kg_extractions 留存每份文档的抽取结果，
删除文档时从剩余文档的抽取中重算受影响实体/关系，不重跑 LLM。

另含实体编辑操作（rename/merge/delete/relation edit）——对齐 LightRAG 编辑 API。
"""

from __future__ import annotations

import json
import logging

from app.db.session import get_session
from app.models import KgEntity, KgExtraction, KgRelation

log = logging.getLogger("shared.kg_merge")

_DESC_SUMMARY_THRESHOLD = 8  # 描述来源数：超过则 LLM 摘要（LightRAG 对齐）


def _jloads(raw: str | None, default):  # type: ignore[no-untyped-def]
    try:
        v = json.loads(raw or "")
        return v if isinstance(v, type(default)) else default
    except (json.JSONDecodeError, TypeError):
        return default


def _resolve_description(desc_sources: dict[str, str]) -> str:
    """<8 条来源直接拼接去重；≥8 条 LLM 摘要（缓存键=全部来源描述，摘要结果随来源集变化自动失效）。"""
    descs: list[str] = []
    for ref in sorted(desc_sources):
        d = (desc_sources[ref] or "").strip()
        if d and d not in descs:
            descs.append(d)
    if not descs:
        return ""
    if len(desc_sources) < _DESC_SUMMARY_THRESHOLD:
        return "\n".join(descs)[:1500]
    joined = "\n".join(f"- {d}" for d in descs)
    try:
        from app.services.knowledge.llm_cache import chat_cached

        return (
            chat_cached(
                f"以下是对同一知识条目的多条来源描述，合并为一段 200 字以内的中文摘要：\n{joined}",
                system="你是知识合并专家，只输出摘要正文。",
                role="extract",
            ).strip()[:1500]
        )
    except Exception as exc:  # noqa: BLE001  无 Key 时拼接兜底（查询链路不因无 Key 失败）
        log.debug("desc summary fallback to concat: %s", exc)
        return "\n".join(descs)[:1500]


def _entity_item(e: KgEntity, degree: dict[str, int]) -> dict:
    return {
        "doc_key": f"kg_entity:{e.repo_id}:{e.workspace}:{e.name}",
        "kind": "kg_entity",
        "repo_id": e.repo_id,
        "workspace": e.workspace,
        "title": e.name,
        "content": f"{e.name}（{e.etype}）：{e.description}",
        "meta": {"etype": e.etype, "degree": degree.get(e.name, 0), "source_refs": _jloads(e.source_refs, []), "workspace": e.workspace},
    }


def _relation_item(r: KgRelation) -> dict:
    return {
        "doc_key": f"kg_relation:{r.repo_id}:{r.workspace}:{r.id}",
        "kind": "kg_relation",
        "repo_id": r.repo_id,
        "workspace": r.workspace,
        "title": f"{r.src_name} -{r.rtype}-> {r.dst_name}",
        "content": f"{r.src_name} -{r.rtype}-> {r.dst_name}：{r.description}",
        "meta": {"rtype": r.rtype, "src": r.src_name, "dst": r.dst_name, "weight": r.weight, "source_refs": _jloads(r.source_refs, []), "workspace": r.workspace},
    }


def rebuild_index(repo_id: int, workspace: str) -> None:
    """实体/关系 → 统一检索表全量替换（scope 内 copy-and-swap）。"""
    from app.services.knowledge.rag import index_documents_bulk

    with get_session() as sess:
        rel_rows = (
            sess.query(KgRelation)
            .filter(KgRelation.repo_id == repo_id, KgRelation.workspace == workspace)
            .all()
        )
        degree: dict[str, int] = {}
        for r in rel_rows:
            degree[r.src_name] = degree.get(r.src_name, 0) + 1
            degree[r.dst_name] = degree.get(r.dst_name, 0) + 1
        ent_rows = sess.query(KgEntity).filter(KgEntity.repo_id == repo_id, KgEntity.workspace == workspace).all()

    ent_items = [_entity_item(e, degree) for e in ent_rows]
    rel_items = [_relation_item(r) for r in rel_rows]
    if workspace:
        # 工作区作用域：两类各自 workspace 级替换
        index_documents_bulk(ent_items, replace_scope_ws=("kg_entity", workspace))
        index_documents_bulk(rel_items, replace_scope_ws=("kg_relation", workspace))
    else:
        # legacy 区按 (kind, repo_id) 替换（保持历史 replace 语义）
        index_documents_bulk(ent_items, replace_scope=("kg_entity", repo_id))
        index_documents_bulk(rel_items, replace_scope=("kg_relation", repo_id))


def apply_extraction(repo_id: int, workspace: str, source_ref: str, doc_title: str, extraction: dict, content_hash: str = "", chunk_count: int = 0, gleaning_rounds: int = 0, reindex: bool = True) -> dict:
    """单文档抽取结果落库（LightRAG merge 对齐）：实体投票合并 + 描述源留存 + 关系证据计数 + 选择性删除。

    返回 {entities, relations, entities_merged, relations_merged, entities_removed}。
    """
    ents = extraction.get("entities") or []
    rels = extraction.get("relations") or []
    # 关系端点也建实体（LightRAG：edge 端点必成节点）
    known = {e.get("name", "").strip() for e in ents}
    for r in rels:
        for endpoint in (r.get("src", ""), r.get("dst", "")):
            ep = endpoint.strip()
            if ep and ep not in known:
                ents.append({"name": ep, "type": "concept", "description": ""})
                known.add(ep)
    names = known
    stats = {"entities": len(ents), "relations": len(rels), "entities_merged": 0, "relations_merged": 0, "entities_removed": 0}

    with get_session() as sess:
        # 1) 关系选择性删除：该 source_ref 的旧边中，新抽取里消失的三元组删掉
        #    （撤边证据时同步撤端点实体的该文档引用，避免 refs 残留导致实体删不掉）
        old_rels = sess.query(KgRelation).filter(KgRelation.repo_id == repo_id, KgRelation.workspace == workspace).all()
        new_rel_keys = {
            (r.get("src", "").strip(), r.get("dst", "").strip(), (r.get("type") or "related").strip() or "related")
            for r in rels
        }
        for row in old_rels:
            refs = _jloads(row.source_refs, [])
            key = (row.src_name, row.dst_name, row.rtype)
            if source_ref in refs and key not in new_rel_keys:
                refs.remove(source_ref)
                if not refs:
                    sess.delete(row)
                else:
                    row.source_refs = json.dumps(refs, ensure_ascii=False)
                    row.weight = float(len(refs))
                for ep_name in (row.src_name, row.dst_name):
                    ent = sess.query(KgEntity).filter(KgEntity.repo_id == repo_id, KgEntity.workspace == workspace, KgEntity.name == ep_name).first()
                    if ent is None:
                        continue
                    erefs = _jloads(ent.source_refs, [])
                    if source_ref in erefs:
                        erefs.remove(source_ref)
                        ent.source_refs = json.dumps(erefs, ensure_ascii=False)

        # 2) 实体 upsert（投票 + 描述源）
        for e in ents:
            name = e["name"].strip()
            etype = (e.get("type") or "concept").strip() or "concept"
            desc = (e.get("description") or "").strip()
            row = sess.query(KgEntity).filter(KgEntity.repo_id == repo_id, KgEntity.workspace == workspace, KgEntity.name == name).first()
            if row is None:
                sess.add(
                    KgEntity(
                        repo_id=repo_id,
                        workspace=workspace,
                        name=name[:250],
                        etype=etype[:64],
                        etype_votes=json.dumps({etype: 1}, ensure_ascii=False),
                        description=desc[:1500],
                        desc_sources=json.dumps({source_ref: desc[:800]}, ensure_ascii=False),
                        source_refs=json.dumps([source_ref], ensure_ascii=False),
                        content_hash=content_hash,
                    )
                )
            else:
                stats["entities_merged"] += 1
                votes = _jloads(row.etype_votes, {})
                votes[etype] = int(votes.get(etype, 0)) + 1
                row.etype_votes = json.dumps(votes, ensure_ascii=False)
                row.etype = (max(votes.items(), key=lambda kv: kv[1])[0] if votes else etype)[:64]
                ds = _jloads(row.desc_sources, {})
                if desc:
                    ds[source_ref] = desc[:800]
                row.desc_sources = json.dumps(ds, ensure_ascii=False)
                refs = _jloads(row.source_refs, [])
                if source_ref not in refs:
                    refs.append(source_ref)
                row.source_refs = json.dumps(refs, ensure_ascii=False)
                row.content_hash = content_hash
                row.description = _resolve_description(ds)
        # 3) 孤儿实体清理：引用只来自本文档（或已被撤空）且新抽取中消失 → 删
        for row in sess.query(KgEntity).filter(KgEntity.repo_id == repo_id, KgEntity.workspace == workspace).all():
            refs = _jloads(row.source_refs, [])
            if (not refs or refs == [source_ref]) and row.name not in names:
                sess.delete(row)
                stats["entities_removed"] += 1
        # 4) 关系 upsert（证据计数）
        for r in rels:
            src, dst = r.get("src", "").strip()[:250], r.get("dst", "").strip()[:250]
            rtype = (r.get("type") or "related").strip()[:64] or "related"
            desc = (r.get("description") or "").strip()
            row = (
                sess.query(KgRelation)
                .filter(KgRelation.repo_id == repo_id, KgRelation.workspace == workspace, KgRelation.src_name == src, KgRelation.dst_name == dst, KgRelation.rtype == rtype)
                .first()
            )
            if row is None:
                sess.add(
                    KgRelation(
                        repo_id=repo_id,
                        workspace=workspace,
                        src_name=src,
                        dst_name=dst,
                        rtype=rtype,
                        description=desc[:1000],
                        desc_sources=json.dumps({source_ref: desc[:600]}, ensure_ascii=False),
                        weight=1.0,
                        source_ref=source_ref,
                        source_refs=json.dumps([source_ref], ensure_ascii=False),
                    )
                )
            else:
                stats["relations_merged"] += 1
                refs = _jloads(row.source_refs, [])
                if source_ref not in refs:
                    refs.append(source_ref)
                row.source_refs = json.dumps(refs, ensure_ascii=False)
                row.weight = float(len(refs))
                ds = _jloads(row.desc_sources, {})
                if desc:
                    ds[source_ref] = desc[:600]
                row.desc_sources = json.dumps(ds, ensure_ascii=False)
                row.description = _resolve_description(ds)[:1000]
                if not row.source_ref:
                    row.source_ref = source_ref
        # 5) 抽取结果留存（删除重建依据）：按 (repo, workspace, source_ref) 幂等 upsert
        ext = (
            sess.query(KgExtraction)
            .filter(KgExtraction.repo_id == repo_id, KgExtraction.workspace == workspace, KgExtraction.source_ref == source_ref)
            .first()
        )
        payload = json.dumps({"title": doc_title, "entities": ents, "relations": rels}, ensure_ascii=False)
        if ext is None:
            sess.add(
                KgExtraction(
                    repo_id=repo_id,
                    workspace=workspace,
                    source_ref=source_ref[:250],
                    content_hash=content_hash,
                    result_json=payload,
                    chunk_count=chunk_count,
                    gleaning_rounds=gleaning_rounds,
                )
            )
        else:
            ext.content_hash = content_hash
            ext.result_json = payload
            ext.chunk_count = chunk_count
            ext.gleaning_rounds = gleaning_rounds
        sess.commit()
    if reindex:
        rebuild_index(repo_id, workspace)
    return stats


def remove_source(repo_id: int, workspace: str, source_ref: str, reindex: bool = True) -> dict:
    """删除一份文档的图谱贡献（LightRAG delete→cache 重建对齐）：从 kg_extractions 重算剩余文档的贡献。"""
    stats = {"entities_removed": 0, "relations_removed": 0, "entities_rebuilt": 0, "relations_rebuilt": 0}
    with get_session() as sess:
        ext = (
            sess.query(KgExtraction)
            .filter(KgExtraction.repo_id == repo_id, KgExtraction.workspace == workspace, KgExtraction.source_ref == source_ref)
            .first()
        )
        doc_result = json.loads(ext.result_json) if ext is not None else {}
        ext_ents = {e.get("name", "").strip() for e in doc_result.get("entities", []) if isinstance(e, dict)}

        # 关系：撤掉本文档证据
        for row in sess.query(KgRelation).filter(KgRelation.repo_id == repo_id, KgRelation.workspace == workspace).all():
            refs = _jloads(row.source_refs, [])
            if source_ref in refs:
                refs.remove(source_ref)
                if not refs:
                    sess.delete(row)
                    stats["relations_removed"] += 1
                else:
                    row.source_refs = json.dumps(refs, ensure_ascii=False)
                    row.weight = float(len(refs))
                    ds = _jloads(row.desc_sources, {})
                    ds.pop(source_ref, None)
                    row.desc_sources = json.dumps(ds, ensure_ascii=False)
                    row.description = _resolve_description(ds)[:1000]
                    stats["relations_rebuilt"] += 1

        # 实体：撤投票/描述/引用，归零即删（含仅靠边端点存活的实体：refs 归零且无边相连 → 删）
        for row in sess.query(KgEntity).filter(KgEntity.repo_id == repo_id, KgEntity.workspace == workspace).all():
            refs = _jloads(row.source_refs, [])
            if source_ref not in refs:
                continue
            refs.remove(source_ref)
            if not refs:
                has_edge = (
                    sess.query(KgRelation)
                    .filter(
                        KgRelation.repo_id == repo_id,
                        KgRelation.workspace == workspace,
                        (KgRelation.src_name == row.name) | (KgRelation.dst_name == row.name),
                    )
                    .first()
                    is not None
                )
                if row.name in ext_ents or not has_edge:
                    sess.delete(row)
                    stats["entities_removed"] += 1
                    continue
            votes = _jloads(row.etype_votes, {})
            ds = _jloads(row.desc_sources, {})
            ds.pop(source_ref, None)
            # 该文档给它投过的票型 -1（从抽取记录反推）
            for e in doc_result.get("entities", []):
                if isinstance(e, dict) and e.get("name", "").strip() == row.name:
                    t = (e.get("type") or "concept").strip() or "concept"
                    votes[t] = int(votes.get(t, 0)) - 1
                    if votes[t] <= 0:
                        votes.pop(t, None)
            row.source_refs = json.dumps(refs, ensure_ascii=False)
            row.etype_votes = json.dumps(votes, ensure_ascii=False)
            row.desc_sources = json.dumps(ds, ensure_ascii=False)
            if votes:
                row.etype = max(votes.items(), key=lambda kv: kv[1])[0][:64]
            row.description = _resolve_description(ds)
            stats["entities_rebuilt"] += 1

        if ext is not None:
            sess.delete(ext)
        sess.commit()
    if reindex:
        rebuild_index(repo_id, workspace)
    return stats


# ---------------- 实体/关系编辑操作（LightRAG 编辑 API 对齐） ----------------


def rename_entity(repo_id: int, workspace: str, old: str, new: str) -> dict:
    """实体改名：级联更新关系端点。"""
    with get_session() as sess:
        row = sess.query(KgEntity).filter(KgEntity.repo_id == repo_id, KgEntity.workspace == workspace, KgEntity.name == old).first()
        if row is None:
            raise LookupError(f"实体不存在: {old}")
        dup = sess.query(KgEntity).filter(KgEntity.repo_id == repo_id, KgEntity.workspace == workspace, KgEntity.name == new).first()
        if dup is not None and dup.id != row.id:
            # 目标名已存在 → 合并
            return merge_entities(repo_id, workspace, [old], new)
        row.name = new[:250]
        for rel in sess.query(KgRelation).filter(KgRelation.repo_id == repo_id, KgRelation.workspace == workspace, KgRelation.src_name == old).all():
            rel.src_name = new[:250]
        for rel in sess.query(KgRelation).filter(KgRelation.repo_id == repo_id, KgRelation.workspace == workspace, KgRelation.dst_name == old).all():
            rel.dst_name = new[:250]
        sess.commit()
    rebuild_index(repo_id, workspace)
    return {"renamed": old, "to": new}


def merge_entities(repo_id: int, workspace: str, sources: list[str], into: str) -> dict:
    """实体合并：投票/描述/引用并入目标，关系端点重定向，重复边合并证据。"""
    with get_session() as sess:
        target = sess.query(KgEntity).filter(KgEntity.repo_id == repo_id, KgEntity.workspace == workspace, KgEntity.name == into).first()
        if target is None:
            raise LookupError(f"目标实体不存在: {into}")
        for src in sources:
            if src == into:
                continue
            row = sess.query(KgEntity).filter(KgEntity.repo_id == repo_id, KgEntity.workspace == workspace, KgEntity.name == src).first()
            if row is None:
                continue
            votes = _jloads(target.etype_votes, {})
            for t, c in _jloads(row.etype_votes, {}).items():
                votes[t] = int(votes.get(t, 0)) + int(c)
            target.etype_votes = json.dumps(votes, ensure_ascii=False)
            if votes:
                target.etype = max(votes.items(), key=lambda kv: kv[1])[0][:64]
            ds = _jloads(target.desc_sources, {})
            ds.update(_jloads(row.desc_sources, {}))
            target.desc_sources = json.dumps(ds, ensure_ascii=False)
            target.description = _resolve_description(ds)
            refs = _jloads(target.source_refs, [])
            refs += [r for r in _jloads(row.source_refs, []) if r not in refs]
            target.source_refs = json.dumps(refs, ensure_ascii=False)
            sess.delete(row)
            # 关系端点重定向 + 重复边合并
            for rel in sess.query(KgRelation).filter(KgRelation.repo_id == repo_id, KgRelation.workspace == workspace, KgRelation.src_name == src).all():
                _redirect_edge(sess, repo_id, workspace, rel, "src", into)
            for rel in sess.query(KgRelation).filter(KgRelation.repo_id == repo_id, KgRelation.workspace == workspace, KgRelation.dst_name == src).all():
                _redirect_edge(sess, repo_id, workspace, rel, "dst", into)
        sess.commit()
    rebuild_index(repo_id, workspace)
    return {"merged": sources, "into": into}


def _redirect_edge(sess, repo_id: int, workspace: str, rel: KgRelation, side: str, into: str) -> None:  # type: ignore[no-untyped-def]
    if side == "src":
        rel.src_name = into[:250]
    else:
        rel.dst_name = into[:250]
    # 重定向后可能撞上已有同三元组边 → 合并证据
    dup = (
        sess.query(KgRelation)
        .filter(
            KgRelation.repo_id == repo_id,
            KgRelation.workspace == workspace,
            KgRelation.src_name == rel.src_name,
            KgRelation.dst_name == rel.dst_name,
            KgRelation.rtype == rel.rtype,
        )
        .all()
    )
    if len(dup) > 1:
        keep, rest = dup[0], dup[1:]
        refs = _jloads(keep.source_refs, [])
        ds = _jloads(keep.desc_sources, {})
        for r in rest:
            refs += [x for x in _jloads(r.source_refs, []) if x not in refs]
            ds.update(_jloads(r.desc_sources, {}))
            sess.delete(r)
        keep.source_refs = json.dumps(refs, ensure_ascii=False)
        keep.weight = float(len(refs))
        keep.desc_sources = json.dumps(ds, ensure_ascii=False)
        keep.description = _resolve_description(ds)[:1000]


def edit_entity(repo_id: int, workspace: str, name: str, etype: str = "", description: str = "") -> dict:
    with get_session() as sess:
        row = sess.query(KgEntity).filter(KgEntity.repo_id == repo_id, KgEntity.workspace == workspace, KgEntity.name == name).first()
        if row is None:
            raise LookupError(f"实体不存在: {name}")
        if etype:
            row.etype = etype[:64]
            votes = _jloads(row.etype_votes, {})
            votes["__manual__" + etype] = votes.get("__manual__" + etype, 0)
            row.etype_votes = json.dumps({etype: max(1, int(votes.get(etype, 0)))}, ensure_ascii=False)
        if description:
            row.description = description[:1500]
        sess.commit()
    rebuild_index(repo_id, workspace)
    return {"edited": name}


def delete_entity(repo_id: int, workspace: str, name: str) -> dict:
    """删实体 + 级联删关联边。"""
    with get_session() as sess:
        row = sess.query(KgEntity).filter(KgEntity.repo_id == repo_id, KgEntity.workspace == workspace, KgEntity.name == name).first()
        if row is None:
            raise LookupError(f"实体不存在: {name}")
        n_rel = (
            sess.query(KgRelation)
            .filter(
                KgRelation.repo_id == repo_id,
                KgRelation.workspace == workspace,
                (KgRelation.src_name == name) | (KgRelation.dst_name == name),
            )
            .delete(synchronize_session=False)
        )
        sess.delete(row)
        sess.commit()
    rebuild_index(repo_id, workspace)
    return {"deleted": name, "relations_removed": int(n_rel or 0)}


def edit_relation(repo_id: int, workspace: str, rel_id: int, rtype: str = "", description: str = "") -> dict:
    with get_session() as sess:
        row = sess.get(KgRelation, rel_id)
        if row is None or row.repo_id != repo_id or row.workspace != workspace:
            raise LookupError(f"关系不存在: {rel_id}")
        if rtype:
            row.rtype = rtype[:64]
        if description:
            row.description = description[:1000]
        sess.commit()
    rebuild_index(repo_id, workspace)
    return {"edited": rel_id}


def delete_relation(repo_id: int, workspace: str, rel_id: int) -> dict:
    with get_session() as sess:
        row = sess.get(KgRelation, rel_id)
        if row is None or row.repo_id != repo_id or row.workspace != workspace:
            raise LookupError(f"关系不存在: {rel_id}")
        sess.delete(row)
        sess.commit()
    rebuild_index(repo_id, workspace)
    return {"deleted": rel_id}


def kg_stats(repo_id: int = 0, workspace: str = "") -> dict:
    """图谱规模（实体/关系/社区/抽取留存），scope 同检索语义。"""
    from sqlalchemy import func

    from app.models import KgCommunity

    with get_session() as sess:
        eq = sess.query(func.count()).select_from(KgEntity)
        rq = sess.query(func.count()).select_from(KgRelation)
        cq = sess.query(func.count()).select_from(KgCommunity)
        xq = sess.query(func.count()).select_from(KgExtraction)
        if workspace:
            eq = eq.filter(KgEntity.workspace == workspace)
            rq = rq.filter(KgRelation.workspace == workspace)
            cq = cq.filter(KgCommunity.workspace == workspace)
            xq = xq.filter(KgExtraction.workspace == workspace)
        elif repo_id:
            eq = eq.filter(KgEntity.repo_id == repo_id)
            rq = rq.filter(KgRelation.repo_id == repo_id)
            cq = cq.filter(KgCommunity.repo_id == repo_id)
            xq = xq.filter(KgExtraction.repo_id == repo_id)
        return {
            "entities": int(eq.scalar() or 0),
            "relations": int(rq.scalar() or 0),
            "communities": int(cq.scalar() or 0),
            "extractions": int(xq.scalar() or 0),
        }


__all__ = [
    "apply_extraction",
    "remove_source",
    "rebuild_index",
    "rename_entity",
    "merge_entities",
    "edit_entity",
    "delete_entity",
    "edit_relation",
    "delete_relation",
    "kg_stats",
]
