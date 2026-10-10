"""KG 社区检测 + 社区报告（LightRAG Leiden communities 对齐，用 networkx Louvain 等价实现）：

- 基础层（level 1）：实体-关系图上 Louvain 聚类（固定随机种子保证确定性——GitNexus 教训），
  networkx 不可用/退化图回退 union-find 连通分量；
- reduce 层（level 2）：跨社区边构造社区图再聚一层，报告由子报告摘要（map-reduce）；
- 报告：map=每社区 LLM 摘要（成员实体+关系描述，token 预算内），Key 未配置回退确定性拼接
  （summary_source=concat，诚实标注）；报告入 rag_documents(kind=kg_community) 供 global 检索消费。
"""

from __future__ import annotations

import json
import logging

from services.shared.db import get_session
from services.shared.models import KgCommunity, KgEntity, KgRelation
from services.shared.tokenizer import truncate_tokens

log = logging.getLogger("shared.kg_communities")

_SEED = 0xC0DE  # 固定种子：同样输入聚类结果确定性（GitNexus Leiden 教训）
_MAP_TOKEN_BUDGET = 6000


def build_communities(repo_id: int = 0, workspace: str = "", llm: bool = True) -> dict:
    """构建/重建 scope 内社区与报告。返回 {clusters, level2, reports}。"""
    with get_session() as sess:
        q = sess.query(KgRelation)
        if workspace:
            q = q.filter(KgRelation.workspace == workspace)
        elif repo_id:
            q = q.filter(KgRelation.repo_id == repo_id)
        rels = q.all()
        eq = sess.query(KgEntity)
        if workspace:
            eq = eq.filter(KgEntity.workspace == workspace)
        elif repo_id:
            eq = eq.filter(KgEntity.repo_id == repo_id)
        ent_desc = {e.name: e.description for e in eq.all()}
        ent_type = {e.name: e.etype for e in eq.all()}

    if not rels:
        return {"clusters": 0, "level2": 0, "reports": 0, "note": "关系图为空，先构建知识图谱"}

    graph = _build_graph(rels)
    base_clusters = _detect(graph)
    reports = 0
    _persist(repo_id, workspace, 1, base_clusters, ent_desc, ent_type, rels, llm=llm, rels_all=rels)
    reports += len(base_clusters)

    # reduce 层：跨社区边建社区图，再聚一层（LightRAG 多层 Leiden 的两级近似）
    n_level2 = 0
    if len(base_clusters) >= 3:
        cluster_of: dict[str, int] = {}
        for cid, members in enumerate(base_clusters):
            for m in members:
                cluster_of[m] = cid
        cgraph: dict[int, dict[int, float]] = {}
        for r in rels:
            a, b = cluster_of.get(r.src_name), cluster_of.get(r.dst_name)
            if a is not None and b is not None and a != b:
                cgraph.setdefault(a, {})
                cgraph.setdefault(b, {})
                cgraph[a][b] = cgraph[a].get(b, 0.0) + float(r.weight)
                cgraph[b][a] = cgraph[b].get(a, 0.0) + float(r.weight)
        meta_clusters = _detect_graph_dict(cgraph, list(range(len(base_clusters))))
        if len(meta_clusters) < len(base_clusters):
            _persist_level2(repo_id, workspace, meta_clusters, base_clusters, ent_desc, llm=llm)
            n_level2 = len(meta_clusters)
            reports += n_level2
    return {"clusters": len(base_clusters), "level2": n_level2, "reports": reports}


def _build_graph(rels: list[KgRelation]):  # type: ignore[no-untyped-def]
    import networkx as nx

    g = nx.Graph()
    for r in rels:
        if g.has_edge(r.src_name, r.dst_name):
            g[r.src_name][r.dst_name]["weight"] += float(r.weight)
        else:
            g.add_edge(r.src_name, r.dst_name, weight=float(r.weight))
    return g


def _detect(graph) -> list[list[str]]:  # type: ignore[no-untyped-def]
    """Louvain（固定种子）→ 失败/空图回退 union-find 连通分量。"""
    try:
        import networkx as nx

        if graph.number_of_nodes() == 0:
            return []
        comms = nx.algorithms.community.louvain_communities(graph, weight="weight", seed=_SEED)
        return sorted([sorted(c) for c in comms if c], key=lambda c: (-len(c), c[0]))
    except Exception as exc:  # noqa: BLE001
        log.warning("louvain failed, fallback to connected components: %s", exc)
        parent: dict[str, str] = {}

        def find(x: str) -> str:
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        try:
            edges_iter = graph.edges()
        except AttributeError:
            edges_iter = []
        for r in edges_iter:
            a, b = find(r[0]), find(r[1])
            if a != b:
                parent[a] = b
        groups: dict[str, list[str]] = {}
        for n in parent:
            groups.setdefault(find(n), []).append(n)
        return sorted(groups.values(), key=lambda c: (-len(c), c[0]))


def _detect_graph_dict(adj: dict[int, dict[int, float]], nodes: list[int]) -> list[list[int]]:  # type: ignore[no-untyped-def]
    import networkx as nx

    g = nx.Graph()
    g.add_nodes_from(nodes)
    for a, nbrs in adj.items():
        for b, w in nbrs.items():
            g.add_edge(a, b, weight=w)
    if g.number_of_edges() == 0:
        return [[n] for n in nodes]
    try:
        comms = nx.algorithms.community.louvain_communities(g, weight="weight", seed=_SEED)
        return sorted([sorted(c) for c in comms if c], key=lambda c: (-len(c), c[0]))
    except Exception:  # noqa: BLE001
        return [[n] for n in nodes]


def _cluster_summary(members: list[str], ent_desc: dict[str, str], ent_type: dict[str, str], edges: list[str], llm: bool) -> tuple[str, str, str]:
    """(title, summary, source)。llm=True 且 Key 可用 → LLM 摘要；否则确定性拼接。"""
    members_md = "\n".join(f"- {m}（{ent_type.get(m, 'concept')}）：{(ent_desc.get(m) or '')[:120]}" for m in members[:40])
    edges_md = "\n".join(f"- {e}" for e in edges[:30])
    prompt = (
        f"以下是知识图谱中一个社区（功能/主题聚类）的成员实体与内部关系：\n\n"
        f"成员实体：\n{truncate_tokens(members_md, _MAP_TOKEN_BUDGET // 2)}\n\n内部关系：\n{truncate_tokens(edges_md, _MAP_TOKEN_BUDGET // 2)}\n\n"
        "输出 JSON：{\"title\":\"社区名（4~8 字）\",\"summary\":\"该主题域的中文摘要（150字内，说清这个簇覆盖什么业务域、核心实体间的关系与测试关注点）\"}"
    )
    if llm:
        try:
            from services.shared.kg_extract import parse_json
            from services.shared.llm_cache import chat_cached

            obj = parse_json(
                chat_cached(prompt, system="你是知识图谱社区报告专家，只输出 JSON。", role="extract")
            )
            title = (obj.get("title") or "").strip()
            summary = (obj.get("summary") or "").strip()
            if title and summary:
                return title[:200], summary[:1500], "llm"
        except Exception as exc:  # noqa: BLE001
            log.debug("community report llm fallback: %s", exc)
    top = members[:3]
    title = "/".join(top)[:200] if top else "未命名社区"
    summary = "；".join(f"{m}（{ent_type.get(m, 'concept')}）" for m in members[:10])
    return title, summary[:1500], "concat"


def _persist(repo_id: int, workspace: str, level: int, clusters: list[list[str]], ent_desc: dict[str, str], ent_type: dict[str, str], rels: list[KgRelation], llm: bool, rels_all: list[KgRelation] | None = None) -> None:
    from services.shared.rag import index_documents_bulk

    edges_by_pair: dict[frozenset[str], str] = {}
    for r in rels:
        edges_by_pair[frozenset((r.src_name, r.dst_name))] = f"{r.src_name} -{r.rtype}-> {r.dst_name}"

    with get_session() as sess:
        sess.query(KgCommunity).filter(KgCommunity.repo_id == repo_id, KgCommunity.workspace == workspace, KgCommunity.level == level).delete(synchronize_session=False)
        sess.commit()

    items: list[dict] = []
    rows: list[KgCommunity] = []
    for cid, members in enumerate(clusters):
        mset = set(members)
        inner_edges = [v for k, v in edges_by_pair.items() if k and k.issubset(mset)]
        title, summary, source = _cluster_summary(members, ent_desc, ent_type, inner_edges, llm)
        row = KgCommunity(
            repo_id=repo_id,
            workspace=workspace,
            level=level,
            cluster_id=cid,
            title=title,
            summary=summary,
            members=json.dumps(members, ensure_ascii=False),
            member_count=len(members),
            summary_source=source,
        )
        rows.append(row)
        items.append(
            {
                "doc_key": f"kg_comm:{workspace}:{level}:{cid}",
                "kind": "kg_community",
                "repo_id": repo_id,
                "workspace": workspace,
                "title": f"[社区{cid}] {title}",
                "content": f"社区 {cid}「{title}」（{len(members)} 实体）：{summary}\n成员：{'、'.join(members[:20])}",
                "meta": {"community": True, "level": level, "cluster_id": cid, "members": members[:40]},
            }
        )
    with get_session() as sess:
        for row in rows:
            sess.add(row)
        sess.commit()
    index_documents_bulk(items, replace_scope_ws=("kg_community", workspace) if workspace else None, replace_scope=("kg_community", repo_id) if not workspace else None)


def _persist_level2(repo_id: int, workspace: str, meta_clusters: list[list[int]], base_clusters: list[list[str]], ent_desc: dict[str, str], llm: bool) -> None:
    from services.shared.rag import index_documents_bulk

    with get_session() as sess:
        sess.query(KgCommunity).filter(KgCommunity.repo_id == repo_id, KgCommunity.workspace == workspace, KgCommunity.level == 2).delete(synchronize_session=False)
        base_rows = {
            r.cluster_id: r
            for r in sess.query(KgCommunity)
            .filter(KgCommunity.repo_id == repo_id, KgCommunity.workspace == workspace, KgCommunity.level == 1)
            .all()
        }
        sess.commit()

    items: list[dict] = []
    rows: list[KgCommunity] = []
    for cid, member_ids in enumerate(meta_clusters):
        members: list[str] = []
        sub_summaries: list[str] = []
        for base_id in member_ids:
            if base_id < len(base_clusters):
                members.extend(base_clusters[base_id][:10])
            base_row = base_rows.get(base_id)
            if base_row is not None:
                sub_summaries.append(f"[{base_row.title}] {base_row.summary[:200]}")
        if not members:
            continue
        # reduce：从子报告摘要生成上层摘要
        title, summary, source = _cluster_summary(members, ent_desc, {}, [], llm=False)
        if llm and sub_summaries:
            try:
                from services.shared.kg_extract import parse_json
                from services.shared.llm_cache import chat_cached

                obj = parse_json(
                    chat_cached(
                        "以下是若干知识图谱子社区的摘要，合并为上层主题域报告。输出 JSON："
                        '{"title":"上层主题域名（4~8字）","summary":"150字内中文摘要"}\n'
                        + "\n".join(sub_summaries[:30]),
                        system="你是知识图谱社区报告专家，只输出 JSON。",
                        role="extract",
                    )
                )
                title = (obj.get("title") or title).strip()[:200]
                summary = (obj.get("summary") or summary).strip()[:1500]
                source = "llm"
            except Exception as exc:  # noqa: BLE001
                log.debug("community reduce llm fallback: %s", exc)
        row = KgCommunity(
            repo_id=repo_id,
            workspace=workspace,
            level=2,
            cluster_id=cid,
            title=title,
            summary=summary,
            members=json.dumps(members[:50], ensure_ascii=False),
            member_count=len(members),
            summary_source=source,
        )
        rows.append(row)
        items.append(
            {
                "doc_key": f"kg_comm:{workspace}:2:{cid}",
                "kind": "kg_community",
                "repo_id": repo_id,
                "workspace": workspace,
                "title": f"[主题域{cid}] {title}",
                "content": f"上层主题域 {cid}「{title}」（聚合 {len(member_ids)} 个子社区）：{summary}",
                "meta": {"community": True, "level": 2, "cluster_id": cid},
            }
        )
    with get_session() as sess:
        for row in rows:
            sess.add(row)
        sess.commit()
    if items:
        index_documents_bulk(items, replace_scope_ws=("kg_community", workspace) if workspace else None, replace_scope=("kg_community", repo_id) if not workspace else None)


def list_communities(repo_id: int = 0, workspace: str = "", level: int = 0) -> list[dict]:
    with get_session() as sess:
        q = sess.query(KgCommunity)
        if workspace:
            q = q.filter(KgCommunity.workspace == workspace)
        elif repo_id:
            q = q.filter(KgCommunity.repo_id == repo_id)
        if level:
            q = q.filter(KgCommunity.level == level)
        rows = q.order_by(KgCommunity.level, KgCommunity.member_count.desc()).all()
    return [
        {
            "id": r.id,
            "level": r.level,
            "cluster_id": r.cluster_id,
            "title": r.title,
            "summary": r.summary,
            "member_count": r.member_count,
            "members": json.loads(r.members) if r.members else [],
            "summary_source": r.summary_source,
        }
        for r in rows
    ]


def community_of(name: str, repo_id: int = 0, workspace: str = "") -> dict[int, int]:
    """实体 → 所属社区映射（图谱按社区着色用）。"""
    with get_session() as sess:
        q = sess.query(KgCommunity).filter(KgCommunity.level == 1)
        if workspace:
            q = q.filter(KgCommunity.workspace == workspace)
        elif repo_id:
            q = q.filter(KgCommunity.repo_id == repo_id)
        out: dict[int, int] = {}
        for row in q.all():
            try:
                members = json.loads(row.members or "[]")
            except json.JSONDecodeError:
                continue
            for m in members:
                out[m] = row.cluster_id
    return out
