"""LightRAG 式异步文档摄入管线：pending → processing → ok/failed 状态机 + 后台线程 worker。

管线（每份文档）：
  解析（parsers，可选）→ 分块（chunking 四策略）→ chunk 持久化（rag_documents kind=kg_chunk）
  → 逐 chunk 抽取+gleaning（llm_cache 键控）→ 三阶段合并（kg_merge.apply_extraction）
  → 社区标记 dirty（下次构建时生效）

状态落 doc_status(kind=kg_doc, workspace)，前端轮询 /api/kg/documents（分页/过滤）。
删除文档：撤 chunk + kg_merge.remove_source（从剩余抽取重建图谱，不重跑 LLM）。
启动恢复：gateway lifespan 调 recover_and_start()，崩溃遗留的 pending/processing 重新入队。
LLM Key 未配置：入队与状态机照常工作，抽取步显式 failed（无 mock 原则），错误信息进状态行。
"""

from __future__ import annotations

import hashlib
import json
import logging
import queue
import threading
from datetime import datetime

from services.shared.db import get_session
from services.shared.models import DocStatus, KgSetting

log = logging.getLogger("shared.doc_pipeline")

_kind = "kg_doc"
_queue: "queue.Queue[str]" = queue.Queue()
_workers: list[threading.Thread] = []
_started = False


def settings_get(key: str, default: str = "") -> str:
    """运行时检索参数（设置页可改，覆盖 .env 默认值）。"""
    with get_session() as sess:
        row = sess.get(KgSetting, key)
        return row.value if row is not None and row.value != "" else default


def settings_set(key: str, value: str) -> None:
    with get_session() as sess:
        sess.merge(KgSetting(key=key, value=value))
        sess.commit()


def settings_all() -> dict[str, str]:
    from services.shared.config import get_settings

    with get_session() as sess:
        overrides = {r.key: r.value for r in sess.query(KgSetting).all()}
    s = get_settings()
    defaults = {
        "chunk_strategy": s.chunk_strategy,
        "chunk_size": str(s.chunk_size),
        "chunk_overlap": str(s.chunk_overlap),
        "chunk_drop_references": str(s.chunk_drop_references),
        "gleaning_rounds": str(s.gleaning_rounds),
        "query_cache_enabled": str(s.query_cache_enabled),
        "user_prompt_prefix": s.user_prompt_prefix,
        "websearch_enabled": str(s.websearch_enabled),
        "websearch_max_results": str(s.websearch_max_results),
    }
    merged = dict(defaults)
    merged.update(overrides)
    return merged


def doc_key_for(workspace: str, title: str) -> str:
    return f"kgdoc:{workspace}:" + hashlib.sha1(f"{workspace}:{title}".encode()).hexdigest()[:16]


def submit_document(workspace: str, title: str, content: str, repo_id: int = 0, filename: str = "", parser: str = "native") -> dict:
    """入队一份文档（同步登记 pending，抽取等重活全部异步）。返回 {doc_key, status}。"""
    title = title.strip()
    if not title:
        raise ValueError("文档标题必填")
    dk = doc_key_for(workspace, title)
    h = hashlib.md5(f"{title}\n{content}".encode("utf-8")).hexdigest()
    with get_session() as sess:
        existing = (
            sess.query(DocStatus)
            .filter(DocStatus.repo_id == repo_id, DocStatus.kind == _kind, DocStatus.doc_key == dk)
            .first()
        )
        if existing is not None:
            existing.status = "pending"
            existing.error = ""
            existing.content_hash = h
            existing.detail = json.dumps({"title": title, "content": content, "filename": filename, "parser": parser, "workspace": workspace}, ensure_ascii=False)
        else:
            sess.add(
                DocStatus(
                    repo_id=repo_id,
                    workspace=workspace,
                    kind=_kind,
                    doc_key=dk,
                    status="pending",
                    detail=json.dumps({"title": title, "content": content, "filename": filename, "parser": parser, "workspace": workspace}, ensure_ascii=False),
                    content_hash=h,
                )
            )
        sess.commit()
    ensure_started()
    _queue.put(dk)
    return {"doc_key": dk, "status": "pending"}


def delete_document(doc_key: str) -> dict:
    """删文档：撤 chunk 索引行 + 从 kg_extractions 重建图谱贡献 + 状态行删除。"""
    from services.shared.kg_merge import remove_source
    from services.shared.rag import remove_documents_by_prefix

    with get_session() as sess:
        row = sess.query(DocStatus).filter(DocStatus.kind == _kind, DocStatus.doc_key == doc_key).first()
        if row is None:
            raise LookupError(f"文档不存在: {doc_key}")
        workspace, repo_id = row.workspace, row.repo_id
        detail = json.loads(row.detail or "{}")
        title = detail.get("title", "")
        sess.delete(row)
        sess.commit()

    # chunk 索引行按 parent 前缀清（chunk doc_key 前缀 = 主 doc_key）
    chunks_removed = remove_documents_by_prefix(_chunk_prefix(doc_key))
    merge_stats = remove_source(repo_id, workspace, source_ref=doc_key) if title else {"note": "无抽取记录"}
    # 主文档实体/关系索引行已在 remove_source→rebuild_index 里刷过；chunk 行清完即收口
    return {"deleted": doc_key, "chunks_removed": chunks_removed, **merge_stats}


def _chunk_prefix(doc_key: str) -> str:
    return f"kgc:{doc_key}:"


def _process(doc_key: str) -> None:
    """worker 主体：pending → processing → ok/failed。"""
    from services.shared import chunking
    from services.shared.kg_extract import extract_document
    from services.shared.kg_merge import apply_extraction

    with get_session() as sess:
        row = sess.query(DocStatus).filter(DocStatus.kind == _kind, DocStatus.doc_key == doc_key).first()
        if row is None:
            return
        row.status = "processing"
        detail = json.loads(row.detail or "{}")
        workspace, repo_id, h = row.workspace, row.repo_id, row.content_hash
        row.updated_at = datetime.now()
        sess.commit()
    title = detail.get("title", "")
    try:
        content = detail.get("content", "")
        # 解析（文件上传时 detail.content 为空、filename 有值 → 读暂存解析）
        if not content and detail.get("tmp_path"):
            from services.shared.parsers import parse_file

            with open(detail["tmp_path"], "rb") as f:
                data = f.read()
            parsed = parse_file(detail.get("filename") or title, data, parser=detail.get("parser", "native"))
            content = parsed.text
            title = title or parsed.title
            detail["parse_meta"] = parsed.meta
        if not content.strip():
            raise ValueError("文档内容为空")
        # 运行时设置（设置页覆盖）
        strategy = settings_get("chunk_strategy", "paragraph")
        chunk_size = int(settings_get("chunk_size", "1200"))
        overlap = int(settings_get("chunk_overlap", "100"))
        drop_refs = settings_get("chunk_drop_references", "True").lower() == "true"
        gleaning = int(settings_get("gleaning_rounds", "1"))

        chunks = chunking.chunk_text(content, strategy=strategy, chunk_size=chunk_size, overlap=overlap, drop_references=drop_refs)
        if not chunks:
            raise ValueError("分块结果为空")

        # chunk 持久化（先清该文档旧 chunk，幂等重建）
        _replace_chunks(workspace, repo_id, doc_key, chunks)

        # 抽取 + gleaning + 合并（llm_cache 键控：未变 chunk 零成本）
        extraction = extract_document(chunks, title, gleaning=gleaning)
        merge_stats = apply_extraction(
            repo_id,
            workspace,
            source_ref=doc_key,
            doc_title=title,
            extraction=extraction,
            content_hash=h,
            chunk_count=len(chunks),
            gleaning_rounds=extraction.get("gleaning_rounds", 0),
        )

        with get_session() as sess:
            row = sess.query(DocStatus).filter(DocStatus.kind == _kind, DocStatus.doc_key == doc_key).first()
            if row is not None:
                row.status = "ok"
                row.error = ""
                row.detail = json.dumps(
                    {**detail, "chunks": len(chunks), "strategy": strategy, "entities": merge_stats.get("entities", 0), "relations": merge_stats.get("relations", 0), "content_chars": len(content)},
                    ensure_ascii=False,
                )
                sess.commit()
        # 社区标记 dirty（查询侧用旧报告，构建入口可见提示）
        settings_set(f"communities_dirty:{workspace}", "1")
        log.info("kg_doc ok %s: chunks=%s entities=%s", doc_key, len(chunks), merge_stats.get("entities", 0))
    except Exception as exc:  # noqa: BLE001
        with get_session() as sess:
            row = sess.query(DocStatus).filter(DocStatus.kind == _kind, DocStatus.doc_key == doc_key).first()
            if row is not None:
                row.status = "failed"
                row.error = str(exc)[:1000]
                row.detail = json.dumps(detail, ensure_ascii=False)
                sess.commit()
        log.warning("kg_doc failed %s: %s", doc_key, exc)


def _replace_chunks(workspace: str, repo_id: int, doc_key: str, chunks: list[dict]) -> None:
    from sqlalchemy import text as _text

    from services.shared.db import get_session as _gs
    from services.shared.rag import index_documents_bulk

    items = []
    for ch in chunks:
        items.append(
            {
                "doc_key": f"kgc:{doc_key}:{ch['index']}",
                "kind": "kg_chunk",
                "repo_id": repo_id,
                "workspace": workspace,
                "title": f"{ch['index'] + 1}/{len(chunks)}",
                "content": ch["content"],
                "meta": {"parent": doc_key, "index": ch["index"], "strategy": ch["meta"].get("strategy", ""), "tokens": ch["tokens"]},
            }
        )
    # 先删该文档旧 chunk（按 doc_key 前缀，避免工作区级误伤他人 chunk）
    with _gs() as sess:
        sess.execute(_text("DELETE FROM rag_documents WHERE kind='kg_chunk' AND doc_key LIKE :p"), {"p": _chunk_prefix(doc_key) + "%"})
        sess.commit()
    index_documents_bulk(items)


def recover_and_start() -> int:
    """启动恢复：pending/processing 重新入队；拉起 worker 线程。返回 worker 数。"""
    global _started
    ensure_started()
    with get_session() as sess:
        stuck = (
            sess.query(DocStatus)
            .filter(DocStatus.kind == _kind, DocStatus.status.in_(("pending", "processing")))
            .all()
        )
        keys = [r.doc_key for r in stuck]
    for k in keys:
        _queue.put(k)
    if keys:
        log.info("doc pipeline recovered %d in-flight document(s)", len(keys))
    return len(_workers)


def ensure_started() -> None:
    """幂等拉起 worker 线程池。"""
    global _started
    if _started:
        return
    from services.shared.config import get_settings

    n = max(1, get_settings().doc_pipeline_workers)
    for i in range(n):
        t = threading.Thread(target=_worker_loop, name=f"kg-doc-worker-{i}", daemon=True)
        t.start()
        _workers.append(t)
    _started = True
    log.info("doc pipeline started with %d worker(s)", n)


def _worker_loop() -> None:
    while True:
        dk = _queue.get()
        try:
            _process(dk)
        except Exception as exc:  # noqa: BLE001  worker 不因单文档异常死亡
            log.exception("doc pipeline worker error: %s", exc)
        finally:
            _queue.task_done()


def list_documents(repo_id: int = 0, workspace: str = "", status: str = "", q: str = "", page: int = 1, page_size: int = 20) -> dict:
    """文档分页列表（状态过滤 + 标题搜索 + 进度元数据）。"""
    from sqlalchemy import func

    with get_session() as sess:
        query = sess.query(DocStatus).filter(DocStatus.kind == _kind)
        count_q = sess.query(func.count()).select_from(DocStatus).filter(DocStatus.kind == _kind)
        if workspace:
            f = DocStatus.workspace == workspace
            query, count_q = query.filter(f), count_q.filter(f)
        elif repo_id:
            f = DocStatus.repo_id == repo_id
            query, count_q = query.filter(f), count_q.filter(f)
        if status:
            f = DocStatus.status == status
            query, count_q = query.filter(f), count_q.filter(f)
        if q.strip():
            # 标题搜索：detail JSON 的 title 字段
            f = DocStatus.detail.contains(q.strip())
            query, count_q = query.filter(f), count_q.filter(f)
        total = int(count_q.scalar() or 0)
        rows = query.order_by(DocStatus.updated_at.desc()).offset(max(0, page - 1) * page_size).limit(page_size).all()
    items = []
    for r in rows:
        detail = json.loads(r.detail or "{}")
        items.append(
            {
                "doc_key": r.doc_key,
                "title": detail.get("title", ""),
                "workspace": r.workspace,
                "repo_id": r.repo_id,
                "status": r.status,
                "error": r.error,
                "chunks": detail.get("chunks", 0),
                "entities": detail.get("entities", 0),
                "relations": detail.get("relations", 0),
                "content_chars": detail.get("content_chars", 0),
                "filename": detail.get("filename", ""),
                "content_hash": r.content_hash,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
        )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def track(doc_key: str) -> dict | None:
    """单文档状态（LightRAG track_status 对齐）。"""
    with get_session() as sess:
        row = sess.query(DocStatus).filter(DocStatus.kind == _kind, DocStatus.doc_key == doc_key).first()
        if row is None:
            return None
        detail = json.loads(row.detail or "{}")
        return {
            "doc_key": row.doc_key,
            "status": row.status,
            "error": row.error,
            "title": detail.get("title", ""),
            "chunks": detail.get("chunks", 0),
            "entities": detail.get("entities", 0),
            "relations": detail.get("relations", 0),
            "workspace": row.workspace,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
