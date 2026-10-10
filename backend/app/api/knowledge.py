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

from fastapi import Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.crud import crud_repos
from app.main import ApiError, app, ok
from app.models import Repos
from app.schemas.kg import KgBuildIn


@app.post("/api/kg/build")
async def kg_build(data: KgBuildIn, db: Session = Depends(get_db)):
    """构建/增量更新文档知识图谱。body {repo_id?}（默认最新仓库）。未配 LLM Key 时逐文档记 failed。"""
    body = data.model_dump()
    repo_id = int(body.get("repo_id") or 0)
    if repo_id:
        repo = crud_repos.get(db, repo_id)
        if repo is None:
            raise ApiError(404, "repo 不存在", 404)
    else:
        repo = db.query(Repos).order_by(Repos.id.desc()).first()
        if repo is None:
            raise ApiError(404, "未接入仓库", 404)
    from app.services.knowledge.docgraph import build_from_repo

    res = build_from_repo(repo.id)
    return ok({"repo_id": repo.id, **res})


@app.get("/api/kg/query")
def kg_query_route(q: str = "", mode: str = "mix", repo_id: int = 0, limit: int = 6):
    """知识图谱双层检索。mode: local（实体级）/ global（主题级）/ mix（默认融合）。"""
    if not q.strip():
        raise ApiError(1001, "q 必填")
    from app.services.knowledge.docgraph import kg_query

    res = kg_query(repo_id, q, mode=mode if mode in ("local", "global", "mix") else "mix", limit=max(1, min(limit, 20)))
    return ok(res)


@app.get("/api/kg/stats")
def kg_stats_route(repo_id: int = 0):
    from app.services.knowledge.docgraph import kg_stats

    return ok(kg_stats(repo_id))


@app.get("/api/knowledge/status")
def knowledge_status(repo_id: int = 0):
    """知识摄入健康视图：按 kind×status 聚合 + 最近失败。"""
    from app.services.knowledge.docstatus import summary

    return ok(summary(repo_id))


@app.get("/api/llm/cache")
def llm_cache_stats():
    from app.services.knowledge.llm_cache import cache_stats

    return ok(cache_stats())


@app.post("/api/llm/cache/clear")
def llm_cache_clear(role: str = ""):
    from app.services.knowledge.llm_cache import clear_cache

    return ok({"cleared": clear_cache(role)})


@app.get("/api/rag/eval")
def rag_eval(k: int = 5, limit: int = 50):
    """检索质量评估：黄金集 recall@k / MRR，混合检索 vs 向量单路。"""
    from app.services.knowledge.rag_eval import evaluate

    return ok(evaluate(k=max(1, min(k, 10)), limit=max(1, min(limit, 200))))
