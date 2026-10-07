"""调用图深度分析（GitNexus check/Processes 的移植）：

- call_cycles: Tarjan SCC 找调用环（循环依赖），对齐 GitNexus `check` 工具。
- entry_chains: 从入度 0 的入口函数沿调用方向找最长链路，GitNexus Processes 的轻量版。
"""

from __future__ import annotations

from collections import defaultdict

from services.shared.db import get_session
from services.shared.models import CallEdges, Functions


def _repo_graph(repo_id: int) -> tuple[dict[int, str], dict[int, list[int]]]:
    with get_session() as sess:
        id_name = {i: n for i, n in sess.query(Functions.id, Functions.name).filter(Functions.repo_id == repo_id).all()}
        adj: dict[int, list[int]] = defaultdict(list)
        if id_name:
            for e in sess.query(CallEdges).filter(CallEdges.caller_id.in_(id_name)).all():
                if e.callee_id in id_name:
                    adj[e.caller_id].append(e.callee_id)
    return id_name, adj


def call_cycles(repo_id: int, max_cycles: int = 20) -> dict:
    """调用环检测：Tarjan 强连通分量，size>1 或自环即成环。返回 {cycles, total}。"""
    id_name, adj = _repo_graph(repo_id)

    index_of: dict[int, int] = {}
    low: dict[int, int] = {}
    on_stack: set[int] = set()
    stack: list[int] = []
    counter = [0]
    cycles: list[list[str]] = []

    # 迭代版 Tarjan（防深递归爆栈）
    for root in id_name:
        if root in index_of:
            continue
        work = [(root, iter(adj.get(root, ())))]
        index_of[root] = low[root] = counter[0]
        counter[0] += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if nxt not in index_of:
                    index_of[nxt] = low[nxt] = counter[0]
                    counter[0] += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, iter(adj.get(nxt, ()))))
                    advanced = True
                    break
                if nxt in on_stack and index_of[nxt] < low[node]:
                    low[node] = index_of[nxt]
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index_of[node]:
                comp: list[int] = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == node:
                        break
                if len(comp) > 1 or node in adj.get(node, ()):
                    cycles.append(sorted(id_name[c] for c in comp))

    cycles.sort(key=len, reverse=True)
    return {"total": len(cycles), "cycles": [{"members": c, "size": len(c)} for c in cycles[:max_cycles]]}


def entry_chains(repo_id: int, max_chains: int = 10, max_len: int = 14) -> dict:
    """执行链路（Processes 轻量版）：入度 0 的入口函数出发的最长简单路径，按长度降序。"""
    id_name, adj = _repo_graph(repo_id)
    indeg: dict[int, int] = {i: 0 for i in id_name}
    for callers in adj.values():
        for c in set(callers):
            if c in indeg:
                indeg[c] += 1
    entries = sorted((i for i, d in indeg.items() if d == 0), key=lambda i: -len(adj.get(i, ())))

    chains: list[dict] = []
    budget = [120_000]

    def longest(start: int) -> list[int]:
        best: list[int] = [start]
        path: list[int] = [start]
        on_path = {start}

        def dfs(node: int) -> None:
            budget[0] -= 1
            if budget[0] <= 0 or len(path) >= max_len:
                return
            for nxt in adj.get(node, ()):
                if nxt in on_path:
                    continue
                path.append(nxt)
                on_path.add(nxt)
                if len(path) > len(best):
                    best[:] = path[:]
                dfs(nxt)
                path.pop()
                on_path.discard(nxt)
                if budget[0] <= 0:
                    return

        dfs(start)
        return best

    for e in entries:
        if len(chains) >= max_chains or budget[0] <= 0:
            break
        chain = longest(e)
        if len(chain) >= 3:  # 至少 3 跳才算"链路"，单调用不值得展示
            chains.append({"entry": id_name[e], "path": [id_name[n] for n in chain], "length": len(chain) - 1})

    chains.sort(key=lambda c: -c["length"])
    return {
        "entry_points": len(entries),
        "chains": chains[:max_chains],
    }
