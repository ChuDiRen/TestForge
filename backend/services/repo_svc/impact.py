"""索引期预计算影响面（blast radius）—— GitNexus impact 思路。

pull/reindex 完成调用图入库后，对每个函数预计算反向可达集（改它会炸多大）：
- reach_count：反向 BFS 可达函数数（调用方、调用方的调用方…）；
- depth_reached：传播最深层；
- score：reach / (全函数-1) 归一化影响分。

查询期 impact_of() 返回某函数变更的受影响函数列表（带深度与置信度 1/depth），
供变更驱动回归与契约 breaking 影响分析复用。
"""

from __future__ import annotations

import logging
from collections import deque

from services.shared.db import get_session
from services.shared.models import CallEdges, FnImpact, Functions

log = logging.getLogger("repo-svc.impact")


def recompute(repo_id: int) -> int:
    """全量重算 repo 影响面，单事务换表内容（读者要么旧要么新）。返回函数数。"""
    with get_session() as sess:
        rows = sess.query(Functions.id, Functions.name).filter(Functions.repo_id == repo_id).all()
        if not rows:
            return 0
        ids = [fid for fid, _ in rows]
        callers_by_id: dict[int, list[int]] = {}
        callees_by_id: dict[int, list[int]] = {}
        for e in sess.query(CallEdges).filter(CallEdges.caller_id.in_(ids)).all():
            callees_by_id.setdefault(e.caller_id, []).append(e.callee_id)
        for e in sess.query(CallEdges).filter(CallEdges.callee_id.in_(ids)).all():
            callers_by_id.setdefault(e.callee_id, []).append(e.caller_id)

        # 同名函数（跨文件同名/历史残留行）聚合为一行，满足 (repo_id, fn_name) 唯一约束
        names_in_order: list[str] = []
        ids_of_name: dict[str, list[int]] = {}
        for fid, fname in rows:
            if fname not in ids_of_name:
                names_in_order.append(fname)
                ids_of_name[fname] = []
            ids_of_name[fname].append(fid)

        total = len(names_in_order)
        computed: list[FnImpact] = []
        for fname in names_in_order:
            name_ids = ids_of_name[fname]
            # 反向 BFS：谁会受我变更影响（同名行全部作为起点）
            seen: dict[int, int] = {i: 0 for i in name_ids}
            q: deque[int] = deque(name_ids)
            max_depth = 0
            reach_ids: set[int] = set()
            while q:
                cur = q.popleft()
                d = seen[cur]
                if d >= _max_depth():
                    continue
                for caller in callers_by_id.get(cur, ()):
                    if caller not in seen:
                        seen[caller] = d + 1
                        max_depth = max(max_depth, d + 1)
                        reach_ids.add(caller)
                        q.append(caller)
            reach = len(reach_ids)
            computed.append(
                FnImpact(
                    repo_id=repo_id,
                    fn_name=fname,
                    callers_count=len({c for i in name_ids for c in callers_by_id.get(i, ())}),
                    callees_count=len({c for i in name_ids for c in callees_by_id.get(i, ())}),
                    reach_count=reach,
                    depth_reached=max_depth,
                    score=round(reach / max(1, total - 1), 4),
                )
            )
        # 原子换内容：删旧插新一个事务
        sess.query(FnImpact).filter(FnImpact.repo_id == repo_id).delete(synchronize_session=False)
        sess.add_all(computed)
        sess.commit()
    log.info("impact recomputed repo=%s fns=%d", repo_id, len(computed))
    return len(computed)


def _max_depth() -> int:
    from services.shared.config import get_settings

    return get_settings().impact_max_depth


def impact_of(repo_id: int, function: str, depth: int = 0) -> list[dict]:
    """变更 blast radius：受 function 变更影响的函数（含自身），按深度升序。

    depth=0 时取 settings.impact_max_depth。confidence = 1/depth（自身为 1.0）。
    受影响集合在线反向 BFS（调用图在库中，代价小）；预计算表服务图展示与排序。
    """
    max_depth = depth or _max_depth()
    with get_session() as sess:
        return _online_bfs(sess, repo_id, function, max_depth)


def _online_bfs(sess, repo_id: int, function: str, max_depth: int) -> list[dict]:  # type: ignore[no-untyped-def]
    fn = (
        sess.query(Functions)
        .filter(Functions.repo_id == repo_id, Functions.name == function)
        .order_by(Functions.id.desc())
        .first()
    )
    if fn is None:
        return []
    id_name = {i: n for i, n in sess.query(Functions.id, Functions.name).filter(Functions.repo_id == repo_id).all()}
    callers: dict[int, list[int]] = {}
    for e in sess.query(CallEdges).filter(CallEdges.callee_id.in_(list(id_name))).all():
        callers.setdefault(e.callee_id, []).append(e.caller_id)

    from collections import deque

    seen: dict[int, int] = {fn.id: 0}
    q: deque[int] = deque([fn.id])
    while q:
        cur = q.popleft()
        d = seen[cur]
        if d >= max_depth:
            continue
        for caller in callers.get(cur, ()):
            if caller not in seen:
                seen[caller] = d + 1
                q.append(caller)
    out = []
    for fid, d in sorted(seen.items(), key=lambda x: x[1]):
        out.append(
            {
                "name": id_name.get(fid, str(fid)),
                "depth": d,
                "confidence": round(1.0 / (d + 1), 3),
                "is_self": fid == fn.id,
            }
        )
    return out


def top_impact(repo_id: int, n: int = 20) -> list[dict]:
    """影响分 Top N（图 API/批量生成圈选用）。"""
    with get_session() as sess:
        rows = (
            sess.query(FnImpact)
            .filter(FnImpact.repo_id == repo_id)
            .order_by(FnImpact.score.desc(), FnImpact.callers_count.desc())
            .limit(n)
            .all()
        )
        return [
            {
                "name": r.fn_name,
                "score": r.score,
                "reach_count": r.reach_count,
                "callers_count": r.callers_count,
                "depth_reached": r.depth_reached,
            }
            for r in rows
        ]
