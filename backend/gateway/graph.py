"""知识图谱：从真实业务关系（仓库/模块/函数/调用/需求/用例/缺陷）构建图数据。

所有节点与边均由数据库真实关系派生，不做任何演示性硬编码。
"""

from __future__ import annotations

import json
from collections import Counter

from fastapi import Request

from gateway.main import ApiError, app, get_session, ok
from services.shared.models import CallEdges, Cases, Defects, Functions, Repos, Requirements


@app.get("/api/graph")
def knowledge_graph(request: Request):
    """知识图谱数据：repo → 模块 → 函数（调用边）→ 用例 ← 需求；用例 → 缺陷。

    查询参数：repo_id（默认最新仓库）、module（模块前缀过滤）、max_functions（函数上限，
    按调用度数取前 N，保证图可渲染）。
    """
    qp = request.query_params
    repo_id = int(qp.get("repo_id") or 0)
    module_prefix = (qp.get("module") or "").strip()
    max_functions = max(10, min(int(qp.get("max_functions") or 120), 300))

    with get_session() as sess:
        repo = sess.get(Repos, repo_id) if repo_id else sess.query(Repos).order_by(Repos.id.desc()).first()
        if repo is None:
            raise ApiError(404, "未接入仓库", 404)

        fns = sess.query(Functions).filter(Functions.repo_id == repo.id).all()
        if module_prefix:
            fns = [f for f in fns if (f.module or "").startswith(module_prefix)]
        if not fns:
            raise ApiError(404, "该范围下没有已索引函数", 404)

        # 索引期预计算物：影响分（blast radius）+ 测试域（功能聚类）
        from services.shared.models import FnCluster, FnImpact

        impact_map = {r.fn_name: r for r in sess.query(FnImpact).filter(FnImpact.repo_id == repo.id).all()}
        cluster_map = {r.fn_name: r.label for r in sess.query(FnCluster).filter(FnCluster.repo_id == repo.id).all()}

        ids = {f.id for f in fns}
        edge_rows = (
            sess.query(CallEdges).filter(CallEdges.caller_id.in_(ids), CallEdges.callee_id.in_(ids)).all()
            if ids
            else []
        )
        deg: Counter[int] = Counter()
        for e in edge_rows:
            deg[e.caller_id] += 1
            deg[e.callee_id] += 1
        def _reach_of(f) -> int:  # type: ignore[no-untyped-def]
            imp = impact_map.get(f.name)
            return imp.reach_count if imp is not None else deg.get(f.id, 0)

        fns_top = sorted(fns, key=lambda f: (-_reach_of(f), f.name))[:max_functions]
        keep = {f.id for f in fns_top}
        call_edges = [(e.caller_id, e.callee_id) for e in edge_rows if e.caller_id in keep and e.callee_id in keep]
        fn_names = {f.name for f in fns_top}

        modules = sorted({f.module for f in fns_top})

        linked_cases = (
            sess.query(Cases)
            .filter(Cases.repo_id == repo.id, Cases.target_function.in_(fn_names))
            .limit(80)
            .all()
        )
        req_codes = {c.source_req for c in linked_cases if c.source_req}
        reqs = sess.query(Requirements).filter(Requirements.code.in_(req_codes)).all() if req_codes else []

        all_defects = sess.query(Defects).limit(200).all()
        case_codes = {c.code for c in linked_cases}
        linked_defects = [
            d for d in all_defects if set(_load_codes(d.case_codes)) & case_codes
        ][:30]

        # ---- 节点 ----
        # 函数节点 id 必须带模块唯一化（同名函数在多个模块很常见），否则前端 ECharts
        # 遇到重复 name/id 直接抛 "Graph nodes have duplicate name or id" 并白屏
        name_counts = Counter(f.name for f in fns_top)
        dup_names = {n for n, c in name_counts.items() if c > 1}

        def _display_name(f) -> str:  # type: ignore[no-untyped-def]
            if f.name not in dup_names:
                return f.name
            return f"{(f.module or "").rsplit(".", 1)[-1]}.{f.name}"

        def _fnode_id(f) -> str:  # type: ignore[no-untyped-def]
            return f"fn:{f.module}:{f.name}"

        fnode_by_fid = {f.id: f for f in fns_top}
        # 同名函数（无法从用例 target_function 定位模块）统一挂到首个同名节点
        first_fnode_by_name: dict[str, str] = {}
        for f in fns_top:
            first_fnode_by_name.setdefault(f.name, _fnode_id(f))

        nodes: list[dict] = [
            {"id": f"repo:{repo.id}", "name": repo.url.rsplit("/", 1)[-1], "category": "仓库", "repo_id": repo.id, "status": repo.status}
        ]
        for m in modules:
            n_fn = sum(1 for f in fns_top if f.module == m)
            nodes.append({"id": f"mod:{m}", "name": m, "category": "模块", "module": m, "函数数": n_fn})
        for f in fns_top:
            imp = impact_map.get(f.name)
            nodes.append({
                "id": _fnode_id(f),
                "name": _display_name(f),
                "category": "函数",
                "module": f.module,
                "language": getattr(f, "language", "") or "python",
                "度数": deg.get(f.id, 0),
                "影响分": imp.score if imp is not None else 0.0,
                "影响半径": imp.reach_count if imp is not None else 0,
                "测试域": cluster_map.get(f.name, ""),
            })
        for r in reqs:
            nodes.append({"id": f"req:{r.code}", "name": r.code, "category": "需求", "标题": r.title, "状态": r.status, "可测性": round(r.testability_score or 0)})
        for c in linked_cases:
            nodes.append({"id": f"case:{c.code}", "name": c.code, "category": "用例", "标题": c.title, "类别": c.category, "状态": c.status})
        for d in linked_defects:
            nodes.append({"id": f"defect:{d.code}", "name": d.code, "category": "缺陷", "标题": d.title, "严重度": d.severity, "状态": d.status})

        # ---- 边 ----
        edges: list[dict] = []
        for m in modules:
            edges.append({"source": f"repo:{repo.id}", "target": f"mod:{m}", "relation": "包含"})
        for f in fns_top:
            edges.append({"source": f"mod:{f.module}", "target": _fnode_id(f), "relation": "包含"})
        for caller, callee in call_edges:
            cf = fnode_by_fid.get(caller)
            ef = fnode_by_fid.get(callee)
            if cf is None or ef is None:
                continue
            edges.append({"source": _fnode_id(cf), "target": _fnode_id(ef), "relation": "调用"})
        for c in linked_cases:
            if c.source_req:
                edges.append({"source": f"req:{c.source_req}", "target": f"case:{c.code}", "relation": "派生"})
            tf = (c.target_function or "").split(".")[-1]
            if tf in fn_names:
                edges.append({"source": f"case:{c.code}", "target": first_fnode_by_name[tf], "relation": "覆盖"})
        for d in linked_defects:
            for cc in _load_codes(d.case_codes):
                if cc in case_codes:
                    edges.append({"source": f"case:{cc}", "target": f"defect:{d.code}", "relation": "暴露"})

        return ok({
            "repo": {"id": repo.id, "url": repo.url},
            "nodes": nodes,
            "edges": edges,
            "modules": modules,
            "stats": {
                "函数": len(fns_top),
                "模块": len(modules),
                "需求": len(reqs),
                "用例": len(linked_cases),
                "缺陷": len(linked_defects),
                "调用边": len(call_edges),
            },
        })


def _load_codes(raw: str | None) -> list[str]:
    try:
        v = json.loads(raw or "[]")
        return v if isinstance(v, list) else []
    except json.JSONDecodeError:
        return []
