"""wiki-builder 业务：分层编译 + 增量重建 + stale 传播。"""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING, Any

from app.core.trace import emit
from app.db.session import get_session
from app.models import CallEdges, Functions, Repos, WikiDeps, WikiPages
from app.services.wiki.summarizer import (
    render_function_page,
    render_module_page,
    render_repo_page,
)

if TYPE_CHECKING:
    from app.services.repo.indexer import FnCard


@dataclasses.dataclass
class GraphCtx:
    """一次 rebuild 的预编译上下文：调用图映射 + 页面索引（避免逐函数查询）。

    page_index 键为 (level, module, function)：函数页按「模块+函数名」唯一定位，
    不同模块的同名函数各占一页，不再共用裸名标题互相覆盖。pages 保留全量页面
    清单（含索引外的历史残留页），供孤儿/重复页清理兜底。"""

    id_name: dict[int, str]
    callers: dict[int, list[str]]
    callees: dict[int, list[str]]
    page_index: dict[tuple[str, str, str], Any]
    pages: list[Any]

log = logging.getLogger("wiki-builder")


def _page_key(p) -> tuple[str, str, str]:  # type: ignore[no-untyped-def]
    return (p.level, p.module or "", p.function or "")


def _fn_title(f) -> str:  # type: ignore[no-untyped-def]
    """函数卡片标题带模块限定：裸名标题会让不同模块的同名函数在检索中互相稀释，
    还会让「一键去重」按标题误删其他模块的真实函数页。"""
    return f"函数 {f.name} · {f.module}"


def rebuild(repo_id: int, from_rev: str, to_rev: str, changed_files: list[str], trace_id: str = "") -> dict:
    """编译/增量重建 repo Wiki。changed_files 非空时仅重建受影响页；调用方页置 stale。

    changed_files == ["__stale__"]：仅重建 stale 页面（一键重建入口）。
    """
    with get_session() as sess:
        repo = sess.get(Repos, repo_id)
        repo_name = repo.url.rsplit("/", 1)[-1].removesuffix(".git") if repo else f"repo-{repo_id}"
        fns = sess.query(Functions).filter(Functions.repo_id == repo_id).all()
        # 缺陷风险点映射一次算好（rebuild 全程复用）：模块页渲染真实缺陷，不再占位假句
        risk_map = _defect_risk_map(repo_id)
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
        pages = sess.query(WikiPages).filter(WikiPages.repo_id == repo_id).order_by(WikiPages.id).all()
        page_index: dict[tuple[str, str, str], Any] = {}
        for p in pages:
            page_index.setdefault(_page_key(p), p)  # 同键历史重复页保留最早一页
        ctx = GraphCtx(id_name=id_name, callers=callers_map, callees=callees_map, page_index=page_index, pages=pages)
        if changed_files == ["__stale__"]:
            # 仅重建 stale 页
            stale_pages = sess.query(WikiPages).filter(WikiPages.repo_id == repo_id, WikiPages.stale.is_(True)).all()
            rebuilt = 0
            for p in stale_pages:
                if p.level == "function" and p.function:
                    f = next((x for x in fns if x.name == p.function and x.module == p.module), None)
                    if f is not None:
                        rebuilt += _upsert_function_page(repo_id, f, ctx, sess)
                elif p.level == "module" and p.module:
                    m_cards = [x for x in fns if x.module == p.module]
                    rebuilt += _upsert_module_page(repo_id, p.module, m_cards, ctx, sess, risk_lines=risk_map.get(p.module))
            total = sess.query(WikiPages).filter(WikiPages.repo_id == repo_id).count()
            sess.commit()
            _index_pages_rag(repo_id)
            _sync_pages_kg(repo_id)
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
                bumped = _upsert_function_page(repo_id, f, ctx, sess)
                pages_rebuilt += 1
                rev_bumped += bumped
            modules = sorted({f.module for f in affected})
            for m in modules:
                m_cards = [f for f in fns if f.module == m]
                pages_rebuilt += 1
                rev_bumped += _upsert_module_page(repo_id, m, m_cards, ctx, sess, risk_lines=risk_map.get(m))
            pages_rebuilt += 1
            rev_bumped += _upsert_repo_page(repo_id, repo_name, fns, ctx, sess)
            total = sess.query(WikiPages).filter(WikiPages.repo_id == repo_id).count()
            sess.commit()
            _index_pages_rag(repo_id)
            _sync_pages_kg(repo_id)
            emit("仓库", "wiki-builder", f"增量重建 repo={repo_id} 受影响页={pages_rebuilt} stale={stale_marked} rev+1={rev_bumped}", trace_id=trace_id or None)
            return {"pages_rebuilt": pages_rebuilt, "pages_stale": stale_marked, "rev_bumped": rev_bumped, "pages_total": total}

        # 全量编译：函数页先行（模块页的依赖边要按索引查函数页），再模块页，最后仓库总览
        _clear_stale(sess, repo_id)
        gc_n = _gc_orphan_pages(sess, ctx, fns)
        pages = 0
        for f in fns:
            _upsert_function_page(repo_id, f, ctx, sess)
            pages += 1
        for m in sorted({f.module for f in fns}):
            m_cards = [f for f in fns if f.module == m]
            _upsert_module_page(repo_id, m, m_cards, ctx, sess, risk_lines=risk_map.get(m))
            pages += 1
        _upsert_repo_page(repo_id, repo_name, fns, ctx, sess)
        pages += 1
        total = sess.query(WikiPages).filter(WikiPages.repo_id == repo_id).count()
        sess.commit()
        _index_pages_rag(repo_id)
        _sync_pages_kg(repo_id)
        emit("仓库", "wiki-builder", f"全量编译 repo={repo_id} 页面={pages} 下线孤儿页={gc_n}", trace_id=trace_id or None)
        return {"pages_rebuilt": pages, "pages_stale": 0, "rev_bumped": 0, "pages_total": total}


def _index_pages_rag(repo_id: int) -> None:
    """wiki 页入统一检索表（混合检索语料），失败不阻塞编译。"""
    try:
        from app.services.knowledge.rag import index_documents_bulk

        with get_session() as sess:
            rows = sess.query(WikiPages).filter(WikiPages.repo_id == repo_id).all()
            items = [
                {
                    "doc_key": f"wiki:{p.id}",
                    "kind": "wiki",
                    "repo_id": repo_id,
                    "title": f"{p.title}",
                    "content": p.content_md[:6000],
                    "meta": {"level": p.level, "module": p.module, "function": p.function, "rev": p.rev, "stale": bool(p.stale)},
                }
                for p in rows
            ]
        index_documents_bulk(items, replace_scope=("wiki", repo_id))
    except Exception as exc:  # noqa: BLE001
        log.warning("wiki rag index failed: %s", exc)


def _sync_pages_kg(repo_id: int) -> None:
    """wiki 编译 → 统一知识管线联动：模块/仓库页提交实体抽取（doc_pipeline 唯一引擎）。

    内容未变的页 hash 短路零成本；wiki 页已删除/改名的自动文档连带清孤儿。
    filename=wiki:{id} 记录溯源（/api/kg/entity/sources 反查用）；doc_key 只由
    workspace+标题派生，改 filename 不产生新文档。失败不阻塞编译。
    函数页低密度不参与抽取（与 /api/kg/build 同一过滤口径）。"""
    try:
        from app.services.knowledge.doc_pipeline import purge_auto_orphans, submit_document

        workspace = f"repo:{repo_id}"
        live_keys: set[str] = set()
        with get_session() as sess:
            pages = (
                sess.query(WikiPages)
                .filter(WikiPages.repo_id == repo_id, WikiPages.level.in_(("module", "repo")))
                .all()
            )
            payloads = [(f"wiki:{p.id}", p.title, p.content_md or "") for p in pages]
        for provenance, title, content in payloads:
            res = submit_document(workspace, title, content, repo_id=repo_id, filename=provenance, source="auto:wiki", priority=10)
            live_keys.add(res["doc_key"])
        purge_auto_orphans(workspace, live_keys, source_prefix="auto:wiki")
    except Exception as exc:  # noqa: BLE001
        log.warning("wiki kg sync failed: %s", exc)


def _defect_risk_map(repo_id: int) -> dict[str, list[str]]:
    """模块 → 未关闭缺陷风险点行（确定性聚合，无 LLM）：defect.case_codes → case 模块。

    Cases.module 直填优先，空则回退 target_function → Functions.module 映射；
    只统计未结案缺陷（resolution 为空），按严重度排序，每模块最多 8 行。
    """
    import json as _json

    from app.models import Cases, Defects, Functions

    try:
        with get_session() as sess:
            defects = sess.query(Defects).filter(Defects.resolution == "").all()
            if not defects:
                return {}
            fn_mod = {name: module for module, name in sess.query(Functions.module, Functions.name).filter(Functions.repo_id == repo_id).all()}
            case_mod: dict[str, str] = {}
            for c in sess.query(Cases.code, Cases.module, Cases.target_function).all():
                case_mod[c[0]] = c[1] or fn_mod.get(c[2] or "", "")
    except Exception as exc:  # noqa: BLE001
        log.warning("defect risk map query failed: %s", exc)
        return {}

    sev_rank = {"致命": 0, "严重": 1, "一般": 2, "轻微": 3}
    by_module: dict[str, list[tuple[int, str, str]]] = {}
    for d in defects:
        try:
            codes = _json.loads(d.case_codes or "[]")
        except _json.JSONDecodeError:
            codes = []
        mods = {case_mod.get(str(code), "") for code in codes if isinstance(code, str)}
        mods.discard("")
        if not mods:
            continue  # 缺陷无关联用例 → 无法定位模块，不硬塞
        for m in mods:
            by_module.setdefault(m, []).append((sev_rank.get(d.severity, 4), d.code, d.title))
    return {
        m: [f"**{code}** {title}" for _, code, title in sorted(items)[:8]]
        for m, items in by_module.items()
    }


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


def _upsert_repo_page(repo_id: int, name: str, fns: list, ctx: "GraphCtx", sess) -> int:  # type: ignore[no-untyped-def]
    cards = [_to_card(f) for f in fns]
    key = ("repo", "", "")
    page = ctx.page_index.get(key) or WikiPages(repo_id=repo_id, level="repo", title=f"{name} 总览")
    content = render_repo_page(name, cards)
    if page.id is None:
        sess.add(page)
        sess.flush()
        page.content_md = content
        page.rev = 1
        ctx.page_index[key] = page
        return 0
    return _upsert(page, content)


def _upsert_module_page(repo_id: int, module: str, fns: list, ctx: "GraphCtx", sess, risk_lines: list[str] | None = None) -> int:  # type: ignore[no-untyped-def]
    cards = [_to_card(f) for f in fns]
    key = ("module", module, "")
    page = ctx.page_index.get(key) or WikiPages(repo_id=repo_id, level="module", title=f"模块 {module}", module=module)
    content = render_module_page(module, cards, risk_lines=risk_lines)
    bump = 0
    if page.id is None:
        sess.add(page)
        sess.flush()
        page.content_md = content
        page.rev = 1
        ctx.page_index[key] = page
    else:
        bump = _upsert(page, content)
    # 模块页依赖其函数卡片页（映射直查，不再逐个查询）
    existing_deps = {
        (d.page_id, d.depends_on_page_id)
        for d in sess.query(WikiDeps).filter(WikiDeps.page_id == page.id).all()
    }
    for f in fns:
        fp = ctx.page_index.get(("function", f.module, f.name))
        if fp is not None and fp.id != page.id and (page.id, fp.id) not in existing_deps:
            sess.add(WikiDeps(page_id=page.id, depends_on_page_id=fp.id))
            existing_deps.add((page.id, fp.id))
    return bump


def _upsert_function_page(repo_id: int, f, ctx: "GraphCtx", sess) -> int:  # type: ignore[no-untyped-def]
    card = _to_card(f)
    content = render_function_page(card, ctx.callers.get(f.id, []), ctx.callees.get(f.id, []))
    key = ("function", f.module, f.name)
    page = ctx.page_index.get(key)
    if page is None:
        page = WikiPages(repo_id=repo_id, level="function", title=_fn_title(f), function=f.name, module=f.module)
        sess.add(page)
        sess.flush()
        page.content_md = content
        page.rev = 1
        ctx.page_index[key] = page  # 新页即注册：同编译轮内后续查找可见
        return 0
    page.title = _fn_title(f)  # 旧裸名标题在此一并纠正
    return _upsert(page, content)


def _mark_stale(caller_fn, ctx: "GraphCtx") -> int:
    n = 0
    fp = ctx.page_index.get(("function", caller_fn.module, caller_fn.name))
    if fp and not fp.stale:
        fp.stale = True
        n += 1
    mp = ctx.page_index.get(("module", caller_fn.module, ""))
    if mp and not mp.stale:
        mp.stale = True
        n += 1
    return n


def _clear_stale(sess, repo_id: int) -> None:  # type: ignore[no-untyped-def]
    sess.query(WikiPages).filter(WikiPages.repo_id == repo_id, WikiPages.stale.is_(True)).update({WikiPages.stale: False}, synchronize_session=False)


def _gc_orphan_pages(sess, ctx: "GraphCtx", fns: list) -> int:  # type: ignore[no-untyped-def]
    """全量编译前下线两类页面：函数/模块已不在当前索引（含非源码目录排除后的残留），
    以及同身份键 (level, module, function) 的历史重复页（裸名时代产物，保留最早一页）。

    遍历全量页面清单而非 page_index——字典会折叠同键页，漏掉索引外的历史残留。
    连带清 WikiDeps 两端；repo 总览页不入清理范围。返回下线页数。
    """
    valid_fns = {(f.module, f.name) for f in fns}
    valid_modules = {f.module for f in fns}
    seen: set[tuple[str, str, str]] = set()
    orphans: list[Any] = []
    for p in ctx.pages:  # 已按 id 升序：同键重复时最早一页存活
        if p.level == "repo":
            continue
        if p.level == "function" and (p.module, p.function) not in valid_fns:
            orphans.append(p)
            continue
        if p.level == "module" and p.module not in valid_modules:
            orphans.append(p)
            continue
        if _page_key(p) in seen:
            orphans.append(p)
            continue
        seen.add(_page_key(p))
    if not orphans:
        return 0
    ids = [p.id for p in orphans]
    sess.query(WikiDeps).filter(
        WikiDeps.page_id.in_(ids) | WikiDeps.depends_on_page_id.in_(ids)
    ).delete(synchronize_session=False)
    n = sess.query(WikiPages).filter(WikiPages.id.in_(ids)).delete(synchronize_session=False)
    return n


def _to_card(f) -> FnCard:  # type: ignore[no-untyped-def]
    from app.services.repo.indexer import FnCard as _FnCard

    return _FnCard(
        module=f.module, name=f.name, signature=f.signature, source=f.source, file=f.file,
        line=f.line, docstring=f.docstring or "", language=getattr(f, "language", "") or "python",
    )
