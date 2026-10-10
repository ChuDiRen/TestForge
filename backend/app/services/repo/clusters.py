"""功能聚类（GitNexus Leiden 思路 → Louvain 社区检测，纯 Python 无编译依赖）。

索引期对调用图（无向化）跑 Louvain 社区检测，把函数聚成"测试域"：
- label 生成：社区内主导模块前缀占比 ≥60% 用模块前缀，否则取枢纽函数名（度数最高）；
- 供用例库分域、批量生成圈选、知识图谱展示。

networkx 缺失时回退连通分量（union-find），聚类粒度变粗但功能可用。
"""

from __future__ import annotations

import logging
from collections import Counter

from app.db.session import get_session
from app.models import CallEdges, FnCluster, Functions

log = logging.getLogger("repo-svc.clusters")


def _communities(nodes: list[str], edges: list[tuple[str, str]]) -> list[list[str]]:
    """社区检测：优先 networkx louvain，缺失回退连通分量。"""
    try:
        import networkx as nx

        g = nx.Graph()
        g.add_nodes_from(nodes)
        g.add_edges_from(edges)
        if len(nodes) >= 2 and g.number_of_edges() > 0:
            return [sorted(c) for c in nx.community.louvain_communities(g, seed=42)]
        return [sorted(c) for c in nx.connected_components(g)]
    except ImportError:
        # union-find 连通分量兜底
        parent: dict[str, str] = {n: n for n in nodes}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for a, b in edges:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
        groups: dict[str, list[str]] = {}
        for n in nodes:
            groups.setdefault(find(n), []).append(n)
        return list(groups.values())


def _label(members: list[str], module_of: dict[str, str], degree: dict[str, int]) -> str:
    """社区标签：主导模块前缀（≥60%）否则枢纽函数名。"""
    prefixes = [module_of.get(m, m.split(".")[0]).split(".")[0] for m in members]
    top, cnt = Counter(prefixes).most_common(1)[0]
    if cnt / max(1, len(members)) >= 0.6:
        return top
    hub = max(members, key=lambda m: degree.get(m, 0))
    return f"{hub}-domain"


def recompute(repo_id: int) -> dict:
    """重算功能聚类，单事务换内容。返回 {functions, clusters}。"""
    with get_session() as sess:
        rows = sess.query(Functions.name, Functions.module).filter(Functions.repo_id == repo_id).all()
        if not rows:
            return {"functions": 0, "clusters": 0}
        # 同名函数去重（跨文件同名/历史残留行），满足 (repo_id, fn_name) 唯一约束
        names: list[str] = []
        module_of: dict[str, str] = {}
        for nm, mod in rows:
            if nm not in module_of:
                names.append(nm)
                module_of[nm] = mod
        id_by_name: dict[str, int] = {}
        for fid, nm in sess.query(Functions.id, Functions.name).filter(Functions.repo_id == repo_id).all():
            id_by_name.setdefault(nm, fid)
        name_by_id = {v: k for k, v in id_by_name.items()}
        edges = []
        degree: Counter[str] = Counter()
        if id_by_name:
            for e in sess.query(CallEdges).filter(CallEdges.caller_id.in_(list(id_by_name.values()))).all():
                a, b = name_by_id.get(e.caller_id), name_by_id.get(e.callee_id)
                if a and b and a != b:
                    edges.append((a, b))
                    degree[a] += 1
                    degree[b] += 1

        comms = _communities(names, edges)
        cluster_rows: list[FnCluster] = []
        for ci, members in enumerate(comms):
            label = _label(members, module_of, degree)
            for m in members:
                cluster_rows.append(FnCluster(repo_id=repo_id, fn_name=m, cluster_id=ci, label=label))
        sess.query(FnCluster).filter(FnCluster.repo_id == repo_id).delete(synchronize_session=False)
        sess.add_all(cluster_rows)
        sess.commit()
    log.info("clusters recomputed repo=%s fns=%d clusters=%d", repo_id, len(names), len(comms))
    return {"functions": len(names), "clusters": len(comms)}


def list_clusters(repo_id: int) -> list[dict]:
    """聚类清单：按簇聚合（label、成员、规模），规模降序。"""
    with get_session() as sess:
        rows = sess.query(FnCluster).filter(FnCluster.repo_id == repo_id).all()
    by_cluster: dict[int, list[FnCluster]] = {}
    for r in rows:
        by_cluster.setdefault(r.cluster_id, []).append(r)
    out: list[dict] = []
    for cid, members in sorted(by_cluster.items()):
        out.append(
            {
                "cluster_id": cid,
                "label": members[0].label if members else "",
                "functions": sorted(m.fn_name for m in members),
                "size": len(members),
            }
        )
    out.sort(key=lambda x: -x["size"])
    return out


def cluster_of(repo_id: int, function: str) -> str:
    with get_session() as sess:
        r = sess.query(FnCluster).filter(FnCluster.repo_id == repo_id, FnCluster.fn_name == function).first()
        return r.label if r else ""
