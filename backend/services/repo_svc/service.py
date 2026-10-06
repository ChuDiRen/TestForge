"""repo-svc 业务逻辑：注册/拉取流水线（克隆 → tree-sitter 索引 → 调用图 → Wiki 编译 → trace）。"""

import logging
from datetime import datetime

from services.repo_svc import gitops
from services.repo_svc.indexer import index_repo, is_supported_file, parse_file
from services.shared.config import GRPC_PORTS
from services.shared.db import get_session
from services.shared.grpc_client import grpc_call
from services.shared.logutil import get_trace_id, new_trace_id
from services.shared.models import CallEdges, Functions, Repos
from services.shared.trace import emit

log = logging.getLogger("repo-svc")


def register(url: str, branch: str, credential_ref: str, webhook: bool) -> dict:
    from services.shared.config import get_settings

    gitops.validate_remote_url(url, allow_local=get_settings().allow_local_repo_url)
    tid = get_trace_id() or new_trace_id()
    local_path = gitops.repo_local_path(url)
    with get_session() as sess:
        repo = Repos(url=url, branch=branch or "main", credential_ref=credential_ref or "", status="接入中", local_path=str(local_path))
        sess.add(repo)
        sess.commit()
        repo_id = repo.id

    steps: list[str] = []
    try:
        rev = gitops.clone(url, local_path, branch or "main")
        steps.append(f"git clone ok @ {rev[:8]}")
        _reindex(repo_id, local_path, rev, steps)
        with get_session() as sess:
            r = sess.get(Repos, repo_id)
            r.status = "已接入"
            r.last_pull = datetime.now()
            r.head_rev = rev
            sess.commit()
        emit("仓库", "repo-svc", f"仓库接入成功 id={repo_id} url={url}", trace_id=tid, extra={"steps": " > ".join(steps)})
    except Exception as exc:  # noqa: BLE001
        with get_session() as sess:
            r = sess.get(Repos, repo_id)
            r.status = f"失败: {str(exc)[:80]}"
            sess.commit()
        emit("仓库", "repo-svc", f"仓库接入失败 id={repo_id}: {exc}", trace_id=tid)
        raise
    return {"id": repo_id, "url": url, "branch": branch or "main", "status": "已接入", "steps": steps}


def pull(repo_id: int) -> dict:
    """拉取 + 增量索引（git diff → 行级定位变更函数 → 仅重建受影响页）。"""
    from pathlib import Path

    tid = get_trace_id() or new_trace_id()
    with get_session() as sess:
        repo = sess.get(Repos, repo_id)
        if repo is None:
            raise KeyError(f"repo {repo_id} not found")
        local_path = repo.local_path or gitops.repo_local_path(repo.url)
        from_rev = repo.head_rev
        url = repo.url
    dest = Path(local_path)
    if not dest.exists():
        raise FileNotFoundError(f"local repo missing: {dest}")
    steps: list[str] = []
    rev = gitops.pull(dest)
    steps.append(f"git pull --ff-only ok @ {rev[:8]}")
    changed = gitops.changed_files(dest, from_rev, rev)
    steps.append(f"changed files: {len(changed)}")
    line_map = gitops.changed_lines(dest, from_rev, rev)
    if line_map:
        steps.append(f"行级 hunk 覆盖 {len(line_map)} 文件（detect_changes 精确归因）")
    # 增量重建（无 diff 时全量校验式重建）
    counts = _reindex(repo_id, dest, rev, steps, changed_files=changed, from_rev=from_rev, line_map=line_map)
    with get_session() as sess:
        r = sess.get(Repos, repo_id)
        r.last_pull = datetime.now()
        r.head_rev = rev
        r.status = "已接入"
        sess.commit()
    emit("仓库", "repo-svc", f"仓库拉取 id={repo_id} rev={rev[:8]} 变更文件={len(changed)}", trace_id=tid)
    return {
        "id": repo_id,
        "url": url,
        "status": "已接入",
        "functions": counts["functions"],
        "call_edges": counts["call_edges"],
        "wiki_pages": counts["wiki_pages"],
        "changed_functions": counts.get("changed_functions", []),
        "impact": counts.get("impact", {}),
        "steps": steps,
    }


def _reindex(
    repo_id: int,
    dest,
    rev: str,
    steps: list[str],
    changed_files: list[str] | None = None,
    from_rev: str = "",
    line_map: dict[str, list[tuple[int, int]]] | None = None,
) -> dict:
    """tree-sitter 索引 + 调用图入库 + Wiki 编译（经 gRPC 调 wiki-builder）。

    原子发布：函数/调用边的全部变更收在单个事务里一次提交——读者要么看到完整旧索引、
    要么看到完整新索引（GitNexus copy-and-swap 语义在 DB 层的等价实现）。
    变更函数精确归因：源码 diff ∪ 函数删除 ∪ diff hunk 行区间命中（GitNexus detect_changes）。
    返回值带 changed_functions：变更驱动回归的输入。
    """
    from services.shared.docstatus import mark as mark_doc

    changed_functions: list[str] = []

    if changed_files:
        changed_src = [dest / f for f in changed_files if is_supported_file(f) and (dest / f).exists()]
        cards = []
        for src_path in changed_src:
            cards.extend(parse_file(src_path, dest))
        steps.append(f"tree-sitter 增量索引: {len(cards)} 函数（{len(changed_src)} 文件）")
    else:
        cards = index_repo(dest)
        steps.append(f"tree-sitter 索引: {len(cards)} 函数")

    with get_session() as sess:
        try:
            old = {f.name: f for f in sess.query(Functions).filter(Functions.repo_id == repo_id).all()}
            if changed_files is not None and changed_files:
                # 增量：仅处理变更文件下的函数
                keep = {}
                for name, f in old.items():
                    if f.file not in changed_files:
                        keep[name] = f
                new_cards = [c for c in cards if c.file in changed_files]
                # 变更文件中被删除/改名的函数同样视为变更（其关联用例必须回归）
                new_names = {c.name for c in new_cards}
                changed_functions = [n for n, f in old.items() if f.file in changed_files and n not in new_names]
            else:
                keep, new_cards = {}, cards

            id_by_name: dict[str, int] = {name: f.id for name, f in keep.items()}
            for card in new_cards:
                row = old.get(card.name)
                if row is not None:
                    if row.source != card.source:
                        changed_functions.append(card.name)
                    row.module, row.signature, row.source, row.file, row.line, row.docstring, row.language = (
                        card.module, card.signature, card.source, card.file, card.line, card.docstring, card.language,
                    )
                    fid = row.id
                else:
                    fn = Functions(
                        repo_id=repo_id, module=card.module, name=card.name, signature=card.signature,
                        source=card.source, file=card.file, line=card.line, docstring=card.docstring,
                        language=card.language,
                    )
                    sess.add(fn)
                    sess.flush()
                    fid = fn.id
                    changed_functions.append(card.name)
                id_by_name[card.name] = fid

            # 行级归因：diff hunk 行区间命中的函数视为变更（源码比对异常时的精确兜底）
            if line_map:
                for card in new_cards:
                    ranges = line_map.get(card.file) or []
                    if ranges and card.name not in changed_functions and any(card.spans(s, e) for s, e in ranges):
                        changed_functions.append(card.name)

            # 调用边（同名解析，跨文件同名取先注册者）
            all_repo_ids = [f.id for f in sess.query(Functions).filter(Functions.repo_id == repo_id).all()]
            name_to_id = {f.name: f.id for f in sess.query(Functions).filter(Functions.repo_id == repo_id).all()}
            if changed_files is None:
                sess.query(CallEdges).filter(CallEdges.caller_id.in_(all_repo_ids)).delete(synchronize_session=False)
            else:
                # 增量：清掉变更文件函数（含被删函数）的旧出边，下面仅按新卡片重建
                cf = set(changed_files)
                stale_caller_ids = [
                    fid for (fid,) in sess.query(Functions.id).filter(Functions.repo_id == repo_id, Functions.file.in_(cf)).all()
                ]
                if stale_caller_ids:
                    sess.query(CallEdges).filter(CallEdges.caller_id.in_(stale_caller_ids)).delete(synchronize_session=False)
            edges = 0
            seen: set[tuple[int, int]] = set()
            for card in new_cards if changed_files is not None else cards:
                caller_id = name_to_id.get(card.name)
                if caller_id is None:
                    continue
                for callee in card.calls:
                    callee_id = name_to_id.get(callee)
                    if callee_id and callee_id != caller_id and (caller_id, callee_id) not in seen:
                        sess.add(CallEdges(caller_id=caller_id, callee_id=callee_id))
                        seen.add((caller_id, callee_id))
                        edges += 1
            sess.commit()  # ← 单事务原子发布点：此前读者全程看旧索引
        except Exception:
            sess.rollback()
            raise
        steps.append(f"索引原子发布: {len(cards)} 函数 / {edges} 新调用边 / 变更函数 {len(changed_functions)}")

    # 检索索引 + 影响面 + 聚类（各自单事务换内容，失败不阻塞接入）
    _index_functions_rag(repo_id, steps)
    impact_n = 0
    clusters_n = 0
    try:
        from services.repo_svc.clusters import recompute as recompute_clusters
        from services.repo_svc.impact import recompute as recompute_impact

        impact_n = recompute_impact(repo_id)
        c_res = recompute_clusters(repo_id)
        clusters_n = int(c_res.get("clusters", 0))
        steps.append(f"影响面预计算 {impact_n} 函数 · 聚类 {clusters_n} 域")
    except Exception as exc:  # noqa: BLE001
        log.warning("impact/cluster precompute failed: %s", exc)
        mark_doc(repo_id, "index", f"impact:{rev[:12]}", "failed", error=str(exc)[:500])
    if changed_files:
        mark_doc(repo_id, "index", f"pull:{rev[:12]}", "ok", detail=f"changed_files={len(changed_files)} changed_functions={len(changed_functions)}")
    else:
        mark_doc(repo_id, "index", f"full:{rev[:12]}", "ok", detail=f"functions={len(cards)}")

    # Wiki 编译（经 gRPC 调 wiki-builder；失败重试 3 次，仍失败不阻塞接入但记录日志）
    pages = 0
    import time as _time

    for attempt in (1, 2, 3):
        try:
            res = grpc_call(
                "wiki-builder",
                GRPC_PORTS["wiki-builder"],
                "WikiBuilder",
                "Rebuild",
                {"repo_id": repo_id, "from_rev": from_rev or "", "to_rev": rev, "changed_files": changed_files or []},
                timeout=120,
            )
            pages = int(res.get("pages_rebuilt", 0))
            steps.append(f"wiki 重建: {pages} 页")
            break
        except Exception as exc:  # noqa: BLE001
            log.warning("wiki rebuild attempt %d failed: %s", attempt, exc)
            if attempt == 3:
                steps.append(f"wiki 重建暂不可用（{str(exc)[:60]}）")
                mark_doc(repo_id, "wiki", f"repo:{repo_id}", "failed", error=str(exc)[:500])
            else:
                _time.sleep(1.0)

    return {
        "functions": len(cards),
        "call_edges": edges,
        "wiki_pages": pages,
        "changed_functions": sorted(set(changed_functions)),
        "impact": {"precomputed": impact_n, "clusters": clusters_n},
    }


def _index_functions_rag(repo_id: int, steps: list[str]) -> None:
    """函数卡片入统一检索表（hybrid 检索的 code 语料），单事务替换。"""
    try:
        from services.shared.rag import index_documents_bulk

        with get_session() as sess:
            fns = sess.query(Functions).filter(Functions.repo_id == repo_id).all()
            items = [
                {
                    "doc_key": f"fn:{repo_id}:{f.name}",
                    "kind": "function",
                    "repo_id": repo_id,
                    "title": f.name,
                    "content": f"{f.name}{f.signature}\n{f.module}\n{(f.docstring or '')[:400]}\n{(f.source or '')[:1200]}",
                    "meta": {"module": f.module, "file": f.file, "line": f.line, "language": f.language or ""},
                }
                for f in fns
            ]
        n = index_documents_bulk(items, replace_scope=("function", repo_id))
        steps.append(f"函数检索索引 {n} 条")
    except Exception as exc:  # noqa: BLE001
        log.warning("function rag index failed: %s", exc)
