"""知识增强路由（本轮借鉴改造的统一入口）：

- POST /api/kg/build           文档知识图谱构建/增量更新（wiki+缺陷+需求 → 实体/关系）
- GET  /api/kg/query           双层检索（local/global/mix）
- GET  /api/kg/stats           图谱规模
- POST /api/repos/{id}/analyze 影响面 + 功能聚类重算（索引期预计算的按需入口）
- GET  /api/repos/{id}/clusters 功能聚类（测试域自动划分）
- GET  /api/functions/{name}/impact  变更 blast radius
- GET  /api/knowledge/status   知识摄入文档状态视图
- GET  /api/llm/cache          LLM 抽取缓存台账 / DELETE 清缓存
- GET  /api/rag/eval           检索质量评估（黄金集：混合 vs 向量单路）
"""

from __future__ import annotations

from fastapi import Request

from gateway.main import ApiError, app, get_session, ok
from services.shared.models import Functions, Repos


@app.post("/api/kg/build")
async def kg_build(request: Request):
    """构建/增量更新文档知识图谱。body {repo_id?}（默认最新仓库）。未配 LLM Key 时逐文档记 failed。"""
    body = await request.json()
    repo_id = int(body.get("repo_id") or 0)
    with get_session() as sess:
        if repo_id:
            repo = sess.get(Repos, repo_id)
            if repo is None:
                raise ApiError(404, "repo 不存在", 404)
        else:
            repo = sess.query(Repos).order_by(Repos.id.desc()).first()
            if repo is None:
                raise ApiError(404, "未接入仓库", 404)
    from services.shared.docgraph import build_from_repo

    res = build_from_repo(repo.id)
    return ok({"repo_id": repo.id, **res})


@app.get("/api/kg/query")
def kg_query_route(q: str = "", mode: str = "mix", repo_id: int = 0, limit: int = 6):
    """知识图谱双层检索。mode: local（实体级）/ global（主题级）/ mix（默认融合）。"""
    if not q.strip():
        raise ApiError(1001, "q 必填")
    from services.shared.docgraph import kg_query

    res = kg_query(repo_id, q, mode=mode if mode in ("local", "global", "mix") else "mix", limit=max(1, min(limit, 20)))
    return ok(res)


@app.get("/api/kg/stats")
def kg_stats_route(repo_id: int = 0):
    from services.shared.docgraph import kg_stats

    return ok(kg_stats(repo_id))


@app.post("/api/repos/{repo_id}/analyze")
def repo_analyze(repo_id: int):
    """按需重算索引期预计算物：影响面（blast radius）+ 功能聚类（测试域）。"""
    with get_session() as sess:
        if sess.get(Repos, repo_id) is None:
            raise ApiError(404, "repo 不存在", 404)
        fn_count = sess.query(Functions).filter(Functions.repo_id == repo_id).count()
    if fn_count == 0:
        raise ApiError(404, "该仓库尚未索引函数，先 pull/接入", 404)
    from services.repo_svc.clusters import recompute as recompute_clusters
    from services.repo_svc.impact import recompute as recompute_impact

    impact_n = recompute_impact(repo_id)
    c_res = recompute_clusters(repo_id)
    return ok({"repo_id": repo_id, "impact_functions": impact_n, "clusters": c_res.get("clusters", 0), "functions": fn_count})


@app.get("/api/repos/{repo_id}/clusters")
def repo_clusters(repo_id: int):
    """功能聚类清单（Louvain 社区 → 测试域）。"""
    from services.repo_svc.clusters import list_clusters

    clusters = list_clusters(repo_id)
    if not clusters:
        raise ApiError(404, "暂无聚类结果，先 POST /api/repos/{id}/analyze", 404)
    return ok(clusters)


@app.get("/api/functions/trace")
def function_trace(src: str, dst: str, repo_id: int = 0, max_depth: int = 10):
    """两符号最短调用路径（GitNexus trace 对齐）：caller→callee BFS 逐跳返回。"""
    from services.repo_svc.impact import trace_path

    rid = repo_id
    if not rid:
        with get_session() as sess:
            row = sess.query(Functions).filter(Functions.name == src).order_by(Functions.id.desc()).first()
            rid = row.repo_id if row else 0
    if not rid:
        raise ApiError(404, "起点函数未索引", 404)
    res = trace_path(rid, src, dst, max_depth)
    if res is None:
        raise ApiError(404, "起点或终点函数未索引", 404)
    return ok(res)


@app.get("/api/functions/{function_name}/impact")
def function_impact(function_name: str, repo_id: int = 0, depth: int = 0):
    """变更影响面：受该函数变更影响的调用方集合（深度+置信度）。repo_id 缺省自动解析。"""
    from services.repo_svc.impact import impact_of

    rid = repo_id
    if not rid:
        with get_session() as sess:
            row = sess.query(Functions).filter(Functions.name == function_name).order_by(Functions.id.desc()).first()
            rid = row.repo_id if row else 0
    if not rid:
        raise ApiError(404, "函数未索引", 404)
    return ok({"function": function_name, "repo_id": rid, "affected": impact_of(rid, function_name, depth)})


@app.get("/api/knowledge/status")
def knowledge_status(repo_id: int = 0):
    """知识摄入健康视图：按 kind×status 聚合 + 最近失败。"""
    from services.shared.docstatus import summary

    return ok(summary(repo_id))


@app.get("/api/llm/cache")
def llm_cache_stats():
    from services.shared.llm_cache import cache_stats

    return ok(cache_stats())


@app.delete("/api/llm/cache")
def llm_cache_clear(role: str = ""):
    from services.shared.llm_cache import clear_cache

    return ok({"cleared": clear_cache(role)})


@app.get("/api/rag/eval")
def rag_eval(k: int = 5, limit: int = 50):
    """检索质量评估：黄金集 recall@k / MRR，混合检索 vs 向量单路。"""
    from services.shared.rag_eval import evaluate

    return ok(evaluate(k=max(1, min(k, 10)), limit=max(1, min(limit, 200))))
