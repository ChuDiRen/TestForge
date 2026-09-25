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
    """拉取 + 增量索引（git diff → 仅重建受影响页）。"""
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
    # 增量重建（无 diff 时全量校验式重建）
    counts = _reindex(repo_id, dest, rev, steps, changed_files=changed, from_rev=from_rev)
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
        "steps": steps,
    }


def _reindex(repo_id: int, dest, rev: str, steps: list[str], changed_files: list[str] | None = None, from_rev: str = "") -> dict:
    """tree-sitter 索引 + 调用图入库 + Wiki 编译（经 gRPC 调 wiki-builder）。

    增量模式（changed_files 非空）只解析变更文件，不再全仓重解析。
    返回值带 changed_functions：源码发生变化的函数名（变更驱动回归的输入）。
    """
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
            id_by_name[card.name] = fid
        sess.commit()

        # 调用边（同名解析，跨文件同名取先注册者）
        if changed_files is None:
            sess.query(CallEdges).filter(
                CallEdges.caller_id.in_([f.id for f in sess.query(Functions).filter(Functions.repo_id == repo_id).all()])
            ).delete(synchronize_session=False)
            sess.commit()
        name_to_id = {f.name: f.id for f in sess.query(Functions).filter(Functions.repo_id == repo_id).all()}
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
        sess.commit()

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
            else:
                _time.sleep(1.0)

    return {"functions": len(cards), "call_edges": edges, "wiki_pages": pages, "changed_functions": changed_functions}
