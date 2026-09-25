"""wiki-builder 业务：分层编译 + 增量重建 + stale 传播。"""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING, Any

from services.shared.db import get_session
from services.shared.llm import get_llm
from services.shared.models import CallEdges, Functions, Repos, WikiDeps, WikiPages
from services.shared.trace import emit
from services.wiki_builder.summarizer import (
    polish,
    render_function_page,
    render_module_page,
    render_repo_page,
)

if TYPE_CHECKING:
    from services.repo_svc.indexer import FnCard


@dataclasses.dataclass
class GraphCtx:
    """一次 rebuild 的预编译上下文：调用图映射 + 页面索引（避免逐函数查询）。"""

    id_name: dict[int, str]
    callers: dict[int, list[str]]
    callees: dict[int, list[str]]
    page_index: dict[tuple[str, str], Any]

log = logging.getLogger("wiki-builder")


def rebuild(repo_id: int, from_rev: str, to_rev: str, changed_files: list[str], trace_id: str = "") -> dict:
    """编译/增量重建 repo Wiki。changed_files 非空时仅重建受影响页；调用方页置 stale。

    changed_files == ["__stale__"]：仅重建 stale 页面（一键重建入口）。
    """
    llm = get_llm()
    with get_session() as sess:
        repo = sess.get(Repos, repo_id)
        repo_name = repo.url.rsplit("/", 1)[-1].removesuffix(".git") if repo else f"repo-{repo_id}"
        fns = sess.query(Functions).filter(Functions.repo_id == repo_id).all()
        # 预编译：调用图映射 + 已有页面索引（整个 rebuild 只查一次，消除 N+1）
        fn_ids = {f.id for f in fns}
        id_name = {f.id: f.name for f in fns}
        callers_map: dict[int, list[str]] = {}
        callees_map: dict[int, list[str]] = {}
        for e in sess.query(CallEdges).filter(CallEdges.caller_id.in_(fn_ids)).all():
            if e.callee_id in id_name:
                callees_map.setdefault(e.caller_id, []).append(id_name[e.callee_id])
        for e in sess.query(CallEdges).filter(CallEdges.callee_id.in_(fn_ids)).all():
            if e.caller_id in id_name:
                callers_map.setdefault(e.callee_id, []).append(id_name[e.caller_id])
        pages = sess.query(WikiPages).filter(WikiPages.repo_id == repo_id).all()
        page_index = {(p.level, p.function or p.module or ""): p for p in pages}
        ctx = GraphCtx(id_name=id_name, callers=callers_map, callees=callees_map, page_index=page_index)
        if changed_files == ["__stale__"]:
            # 仅重建 stale 页
            stale_pages = sess.query(WikiPages).filter(WikiPages.repo_id == repo_id, WikiPages.stale.is_(True)).all()
            rebuilt = 0
            for p in stale_pages:
                if p.level == "function" and p.function:
                    f = next((x for x in fns if x.name == p.function), None)
                    if f is not None:
                        rebuilt += _upsert_function_page(repo_id, f, llm, ctx, sess)
                elif p.level == "module" and p.module:
                    m_cards = [x for x in fns if x.module == p.module]
                    rebuilt += _upsert_module_page(repo_id, p.module, m_cards, llm, ctx, sess)
            total = sess.query(WikiPages).filter(WikiPages.repo_id == repo_id).count()
            sess.commit()
            emit("仓库", "wiki-builder", f"重建 stale 页 repo={repo_id} rev+1={rebuilt}", trace_id=trace_id or None)
            return {"pages_rebuilt": len(stale_pages), "pages_stale": 0, "rev_bumped": rebuilt, "pages_total": total}

        if changed_files:
            # 增量：受影响函数 = 变更文件下的函数
            affected = [f for f in fns if f.file in changed_files]
            affected_ids = {f.id for f in affected}
            # stale 传播：调用方（跨文件）页置 stale
            caller_rows = (
                sess.query(CallEdges).filter(CallEdges.callee_id.in_(affected_ids)).all()
                if affected_ids
                else []
            )
            caller_ids = {e.caller_id for e in caller_rows} - affected_ids
            stale_marked = 0
            for cid in caller_ids:
                caller = sess.get(Functions, cid)
                if caller is None:
                    continue
                stale_marked += _mark_stale(caller, ctx)
            sess.commit()

            # 仅重建受影响页（函数卡片 + 所属模块页 + 仓库总览）
            pages_rebuilt = 0
            rev_bumped = 0
            for f in affected:
                bumped = _upsert_function_page(repo_id, f, llm, ctx, sess)
                pages_rebuilt += 1
                rev_bumped += bumped
            modules = sorted({f.module for f in affected})
            for m in modules:
                m_cards = [f for f in fns if f.module == m]
                pages_rebuilt += 1
                rev_bumped += _upsert_module_page(repo_id, m, m_cards, llm, ctx, sess)
            pages_rebuilt += 1
            rev_bumped += _upsert_repo_page(repo_id, repo_name, fns, llm, ctx, sess)
            total = sess.query(WikiPages).filter(WikiPages.repo_id == repo_id).count()
            sess.commit()
            emit("仓库", "wiki-builder", f"增量重建 repo={repo_id} 受影响页={pages_rebuilt} stale={stale_marked} rev+1={rev_bumped}", trace_id=trace_id or None)
            return {"pages_rebuilt": pages_rebuilt, "pages_stale": stale_marked, "rev_bumped": rev_bumped, "pages_total": total}

        # 全量编译
        _clear_stale(sess, repo_id)
        pages = 0
        _upsert_repo_page(repo_id, repo_name, fns, llm, ctx, sess)
        pages += 1
        for m in sorted({f.module for f in fns}):
            m_cards = [f for f in fns if f.module == m]
            _upsert_module_page(repo_id, m, m_cards, llm, ctx, sess)
            pages += 1
        for f in fns:
            _upsert_function_page(repo_id, f, llm, ctx, sess)
            pages += 1
        total = sess.query(WikiPages).filter(WikiPages.repo_id == repo_id).count()
        sess.commit()
        emit("仓库", "wiki-builder", f"全量编译 repo={repo_id} 页面={pages}", trace_id=trace_id or None)
        return {"pages_rebuilt": pages, "pages_stale": 0, "rev_bumped": 0, "pages_total": total}


def _upsert(page: WikiPages, content: str) -> int:
    """写页面内容：新页 rev=1；已有页内容变化才 rev+1。返回 bump 数。"""
    if page.id is None:
        return 0
    if page.content_md != content:
        page.rev += 1
        page.content_md = content
        page.stale = False
        return 1
    page.stale = False
    return 0


def _upsert_repo_page(repo_id: int, name: str, fns: list, llm, ctx: "GraphCtx", sess) -> int:  # type: ignore[no-untyped-def]
    cards = [_to_card(f) for f in fns]
    page = ctx.page_index.get(("repo", "")) or WikiPages(repo_id=repo_id, level="repo", title=f"{name} 总览")
    content = polish(llm, render_repo_page(name, cards), "repo")
    if page.id is None:
        sess.add(page)
        sess.flush()
        page.content_md = content
        page.rev = 1
        return 0
    return _upsert(page, content)


def _upsert_module_page(repo_id: int, module: str, fns: list, llm, ctx: "GraphCtx", sess) -> int:  # type: ignore[no-untyped-def]
    cards = [_to_card(f) for f in fns]
    page = ctx.page_index.get(("module", module)) or WikiPages(repo_id=repo_id, level="module", title=f"模块 {module}", module=module)
    content = polish(llm, render_module_page(module, cards), "module")
    bump = 0
    if page.id is None:
        sess.add(page)
        sess.flush()
        page.content_md = content
        page.rev = 1
    else:
        bump = _upsert(page, content)
    # 模块页依赖其函数卡片页（映射直查，不再逐个查询）
    existing_deps = {
        (d.page_id, d.depends_on_page_id)
        for d in sess.query(WikiDeps).filter(WikiDeps.page_id == page.id).all()
    }
    for f in fns:
        fp = ctx.page_index.get(("function", f.name))
        if fp is not None and fp.id != page.id and (page.id, fp.id) not in existing_deps:
            sess.add(WikiDeps(page_id=page.id, depends_on_page_id=fp.id))
            existing_deps.add((page.id, fp.id))
    return bump


def _upsert_function_page(repo_id: int, f, llm, ctx: "GraphCtx", sess) -> int:  # type: ignore[no-untyped-def]
    card = _to_card(f)
    content = polish(
        llm,
        render_function_page(card, ctx.callers.get(f.id, []), ctx.callees.get(f.id, [])),
        "function",
    )
    page = ctx.page_index.get(("function", f.name)) or WikiPages(repo_id=repo_id, level="function", title=f"函数 {f.name}", function=f.name, module=f.module)
    if page.id is None:
        sess.add(page)
        sess.flush()
        page.content_md = content
        page.rev = 1
        return 0
    return _upsert(page, content)


def _mark_stale(caller_fn, ctx: "GraphCtx") -> int:
    n = 0
    fp = ctx.page_index.get(("function", caller_fn.name))
    if fp and not fp.stale:
        fp.stale = True
        n += 1
    mp = ctx.page_index.get(("module", caller_fn.module))
    if mp and not mp.stale:
        mp.stale = True
        n += 1
    return n


def _clear_stale(sess, repo_id: int) -> None:  # type: ignore[no-untyped-def]
    sess.query(WikiPages).filter(WikiPages.repo_id == repo_id, WikiPages.stale.is_(True)).update({WikiPages.stale: False}, synchronize_session=False)


def _to_card(f) -> FnCard:  # type: ignore[no-untyped-def]
    from services.repo_svc.indexer import FnCard as _FnCard

    return _FnCard(
        module=f.module, name=f.name, signature=f.signature, source=f.source, file=f.file,
        line=f.line, docstring=f.docstring or "", language=getattr(f, "language", "") or "python",
    )
