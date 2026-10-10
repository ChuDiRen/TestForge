"""代码分析端点：影响面重算 / 测试域聚类 / 调用环 / 入口链 / 执行流 / 变更检测 / 跨函数追溯。"""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.crud import crud_repos
from app.main import ApiError, app, ok
from app.models import Functions


@app.post("/api/repos/{repo_id}/analyze")
def repo_analyze(repo_id: int, db: Session = Depends(get_db)):
    """按需重算索引期预计算物：影响面（blast radius）+ 功能聚类（测试域）。"""
    if crud_repos.get(db, repo_id) is None:
        raise ApiError(404, "repo 不存在", 404)
    fn_count = db.query(Functions).filter(Functions.repo_id == repo_id).count()
    if fn_count == 0:
        raise ApiError(404, "该仓库尚未索引函数，先 pull/接入", 404)
    from app.services.repo.clusters import recompute as recompute_clusters
    from app.services.repo.impact import recompute as recompute_impact

    impact_n = recompute_impact(repo_id)
    c_res = recompute_clusters(repo_id)
    return ok({"repo_id": repo_id, "impact_functions": impact_n, "clusters": c_res.get("clusters", 0), "functions": fn_count})


@app.get("/api/repos/{repo_id}/clusters")
def repo_clusters(repo_id: int):
    """功能聚类清单（Louvain 社区 → 测试域）。"""
    from app.services.repo.clusters import list_clusters

    clusters = list_clusters(repo_id)
    if not clusters:
        raise ApiError(404, "暂无聚类结果，先 POST /api/repos/{id}/analyze", 404)
    return ok(clusters)


@app.get("/api/repos/{repo_id}/cycles")
def repo_cycles(repo_id: int, max_cycles: int = 20):
    """调用环检测（GitNexus check 移植）：Tarjan SCC，循环依赖清单。"""
    from app.services.repo.graph_analysis import call_cycles

    return ok(call_cycles(repo_id, max_cycles))


@app.get("/api/repos/{repo_id}/chains")
def repo_chains(repo_id: int, max_chains: int = 10, max_len: int = 14):
    """执行链路（GitNexus Processes 轻量版）：入口函数出发的最长调用链。"""
    from app.services.repo.graph_analysis import entry_chains

    return ok(entry_chains(repo_id, max_chains, max_len))


@app.get("/api/repos/{repo_id}/processes")
def repo_processes(repo_id: int, max_processes: int = 8, max_len: int = 14):
    """执行流识别（GitNexus Processes 移植）：入度 0 的入口函数出发的最长调用链组织成
    「入口 → 传递 → 出口」的业务执行流。出口 = 链上触达 DB/缓存/HTTP/外部调用的函数
    （按函数名与模块路径启发式识别）。供集成测试生成参考业务旅程。"""
    from app.services.repo.graph_analysis import processes

    return ok(processes(repo_id, max_processes=max_processes, max_len=max_len))


@app.get("/api/repos/{repo_id}/changes")
def repo_changes(repo_id: int, scope: str = "unstaged", base_ref: str = ""):
    """变更影响检测（GitNexus detect_changes 移植）：git diff → 受影响函数 → 调用方/风险。"""
    from app.services.repo.changes import detect_changes

    try:
        return ok(detect_changes(repo_id, scope=scope, base_ref=base_ref))
    except FileNotFoundError as e:
        raise ApiError(404, str(e), 404)
    except LookupError as e:
        raise ApiError(404, str(e), 404)


@app.get("/api/functions/trace")
def function_trace(src: str, dst: str, repo_id: int = 0, max_depth: int = 10, db: Session = Depends(get_db)):
    """两符号最短调用路径（GitNexus trace 对齐）：caller→callee BFS 逐跳返回。"""
    from app.services.repo.impact import trace_path

    rid = repo_id
    if not rid:
        row = db.query(Functions).filter(Functions.name == src).order_by(Functions.id.desc()).first()
        rid = row.repo_id if row else 0
    if not rid:
        raise ApiError(404, "起点函数未索引", 404)
    res = trace_path(rid, src, dst, max_depth)
    if res is None:
        raise ApiError(404, "起点或终点函数未索引", 404)
    return ok(res)


@app.get("/api/functions/{function_name}/impact")
def function_impact(function_name: str, repo_id: int = 0, depth: int = 0, db: Session = Depends(get_db)):
    """变更影响面：受该函数变更影响的调用方集合（深度+置信度）。repo_id 缺省自动解析。

    certainty 语义（对齐 GitNexus epistemic honesty）：调用图来自 tree-sitter 静态分析，
    影响集合是静态图上的精确闭包；动态分发/反射调用不可见——对运行时真实影响而言是下界。
    """
    from app.services.repo.impact import impact_of

    rid = repo_id
    if not rid:
        row = db.query(Functions).filter(Functions.name == function_name).order_by(Functions.id.desc()).first()
        rid = row.repo_id if row else 0
    if not rid:
        raise ApiError(404, "函数未索引", 404)
    return ok({
        "function": function_name,
        "repo_id": rid,
        "affected": impact_of(rid, function_name, depth),
        "certainty": "lower-bound",
        "basis": "静态调用图（tree-sitter）上的精确闭包；动态分发/反射调用不可见，运行时真实影响可能更大",
    })
