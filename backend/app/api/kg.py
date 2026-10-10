"""LightRAG 全量移植路由（2026-10）：

文档管理（异步管线）
- POST   /api/kg/documents/upload        multipart 文件上传（pdf/docx/txt/md/csv）→ 异步管线
- POST   /api/kg/documents               纯文本入库 → 异步管线
- GET    /api/kg/documents               分页列表（status/workspace/q 过滤）
- GET    /api/kg/documents/track         单文档状态（track_status 对齐）
- GET    /api/kg/documents/chunks        某文档的分块清单
- DELETE /api/kg/documents               删除（撤 chunk + 从抽取缓存重建图谱）

查询
- POST   /api/kg/search                  六模式检索（naive/local/global/hybrid/mix）纯召回 + contexts
- POST   /api/kg/query/stream            SSE：检索 + 流式回答 + 最终 contexts（RAGAS-ready）
- GET    /api/kg/graph                   文档图谱（search/focus+depth 子图/max_nodes）
- GET    /api/kg/entity/exists           实体存在性
- PATCH/DELETE /api/kg/entity            实体编辑（改名/属性/删除级联）
- POST   /api/kg/entity/merge            实体合并
- PATCH/DELETE /api/kg/relation          关系编辑
- POST   /api/kg/communities/build       社区检测 + 报告（map-reduce）
- GET    /api/kg/communities             社区清单
- GET    /api/kg/export                  导出（entities/relations/communities/chunks/graph × csv/md/json/xlsx/graphml/zip）
- GET    /api/kg/workspaces              工作区清单
- GET/PUT /api/kg/settings               运行时检索参数（设置页）
- DELETE /api/kg/query-cache             清查询答案缓存

Ollama 兼容（/ollama/*，不在 /api 认证中间件内，模块内自校验）
- GET    /ollama/api/version | tags
- POST   /ollama/api/chat | generate | embeddings
"""

from __future__ import annotations

import base64
import json
import tempfile
import time
from pathlib import Path

from fastapi import Request
from fastapi.responses import StreamingResponse

from app.api.envelope import ApiError, ok
from app.main import app, get_session
from app.models import KgEntity, KgRelation
from app.schemas.kg import (
    KgCommunitiesBuildIn,
    KgDocumentCreateIn,
    KgEntityEditIn,
    KgEntityMergeIn,
    KgQueryStreamIn,
    KgRelationEditIn,
    KgSearchIn,
    KgSettingsIn,
)


def _require_admin(request: Request) -> None:
    user = getattr(request.state, "user", None)
    if user is None or user.get("role") != "admin":
        raise ApiError(403, "只读账号无权执行该操作", 403)


def _resolve_ws(request_body: dict) -> tuple[int, str]:
    repo_id = int(request_body.get("repo_id") or 0)
    workspace = (request_body.get("workspace") or "").strip()
    return repo_id, workspace


# ---------------- 文档管理 ----------------


@app.post("/api/kg/documents")
async def kg_create_document(request: Request, data: KgDocumentCreateIn):
    """纯文本入库（异步管线）：{title, content, workspace?, repo_id?}。"""
    _require_admin(request)
    body = data.model_dump()
    title = (body.get("title") or "").strip()
    content = (body.get("content") or "").strip()
    if not title or not content:
        raise ApiError(400, "title 与 content 必填", 400)
    from app.services.knowledge.doc_pipeline import submit_document

    repo_id, workspace = _resolve_ws(body)
    workspace = workspace or f"repo:{repo_id}" if repo_id else (workspace or "default")
    res = submit_document(workspace, title, content, repo_id=repo_id)
    return ok(res)


@app.post("/api/kg/documents/upload")
async def kg_upload_document(request: Request):
    """multipart 文件上传（pdf/docx/txt/md/csv）→ 暂存 → 异步管线解析+分块+抽取。"""
    _require_admin(request)
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise ApiError(400, "multipart 字段 file 必填", 400)
    filename = str(getattr(upload, "filename", "doc.txt"))
    data = await upload.read()
    if not data:
        raise ApiError(400, "上传文件为空", 400)
    workspace = (str(form.get("workspace") or "").strip() or "default")
    repo_id = int(str(form.get("repo_id") or "0") or "0")
    parser = str(form.get("parser") or "native")
    title = str(form.get("title") or "").strip()

    suffix = Path(filename).suffix.lower()
    if suffix not in (".txt", ".md", ".markdown", ".rst", ".csv", ".pdf", ".docx"):
        raise ApiError(422, f"不支持的文件类型 {suffix}（支持 txt/md/rst/csv/pdf/docx）", 422)

    from app.core.sensitive import scan_sensitive
    from app.services.knowledge.doc_pipeline import submit_document

    # 写入侧敏感信息门卫（与 URL 导入同规）
    if suffix in (".txt", ".md", ".markdown", ".rst", ".csv"):
        hits = scan_sensitive(data.decode("utf-8", errors="replace"))
        if hits:
            raise ApiError(422, f"文档命中敏感信息（{'、'.join(hits)}），拒绝入库", 422)

    tmp_dir = Path(tempfile.gettempdir()) / "testforge_kg_uploads"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / f"{int(time.time() * 1000)}_{filename.replace('/', '_')}"
    tmp_path.write_bytes(data)

    from app.services.knowledge.parsers import parse_file

    probe = parse_file(filename, data, parser=parser)
    title = title or probe.title or Path(filename).stem
    res = submit_document(workspace, title, "", repo_id=repo_id, filename=filename, parser=parser)
    # 管线从 tmp_path 读文件（detail 携带），这里补记
    from app.db.session import get_session as _gs
    from app.models import DocStatus as _DS

    with _gs() as sess:
        row = sess.query(_DS).filter(_DS.kind == "kg_doc", _DS.doc_key == res["doc_key"]).first()
        if row is not None:
            detail = json.loads(row.detail or "{}")
            detail["tmp_path"] = str(tmp_path)
            detail["content"] = probe.text if suffix in (".txt", ".md", ".markdown", ".rst", ".csv") else ""
            row.detail = json.dumps(detail, ensure_ascii=False)
            sess.commit()
    return ok({**res, "title": title, "filename": filename, "parse_meta": probe.meta})


@app.get("/api/kg/documents")
def kg_list_documents(request: Request, repo_id: int = 0, workspace: str = "", status: str = "", q: str = "", page: int = 1, page_size: int = 20):
    from app.services.knowledge.doc_pipeline import list_documents

    return ok(list_documents(repo_id=repo_id, workspace=workspace, status=status, q=q, page=max(1, page), page_size=min(100, max(1, page_size))))


@app.get("/api/kg/documents/track")
def kg_track_document(doc_key: str):
    from app.services.knowledge.doc_pipeline import track

    res = track(doc_key)
    if res is None:
        raise ApiError(404, "文档不存在", 404)
    return ok(res)


@app.get("/api/kg/documents/chunks")
def kg_document_chunks(doc_key: str, workspace: str = ""):
    from app.services.knowledge.kg_query import list_chunks

    return ok(list_chunks(doc_key, workspace=workspace))


@app.get("/api/kg/chunk-content")
def kg_chunk_content(doc_key: str):
    """单个 chunk 全文（文档管线「查看分块」抽屉用）。"""
    from sqlalchemy import text

    from app.services.knowledge.rag import _tables_ready

    with get_session() as sess:
        if not _tables_ready(sess):
            raise ApiError(404, "索引未就绪", 404)
        row = sess.execute(text("SELECT title, content FROM rag_documents WHERE doc_key = :k AND kind = 'kg_chunk'"), {"k": doc_key}).first()
    if row is None:
        raise ApiError(404, "chunk 不存在", 404)
    return ok({"doc_key": doc_key, "title": row[0], "content": row[1]})


@app.delete("/api/kg/documents")
def kg_delete_document(request: Request, doc_key: str):
    _require_admin(request)
    try:
        from app.services.knowledge.doc_pipeline import delete_document

        return ok(delete_document(doc_key))
    except LookupError as e:
        raise ApiError(404, str(e), 404)


# ---------------- 查询 ----------------


@app.post("/api/kg/search")
async def kg_search_route(data: KgSearchIn):
    """六模式纯召回（contexts 供 RAGAS / 生成上下文包）。"""
    body = data.model_dump()
    query = (body.get("query") or body.get("q") or "").strip()
    if not query:
        raise ApiError(1001, "query 必填")
    from app.services.knowledge.kg_query import kg_search

    repo_id, workspace = _resolve_ws(body)
    res = kg_search(
        query,
        mode=str(body.get("mode") or "mix"),
        repo_id=repo_id,
        workspace=workspace,
        top_k=max(1, min(int(body.get("top_k") or 8), 30)),
        websearch=bool(body.get("websearch")),
    )
    return ok(res)


@app.post("/api/kg/query/stream")
async def kg_query_stream(data: KgQueryStreamIn):
    """SSE：retrieved → delta* → done（含 contexts/references/answer）。"""
    body = data.model_dump()
    query = (body.get("query") or body.get("q") or "").strip()
    if not query:
        raise ApiError(1001, "query 必填")
    from app.services.knowledge.kg_query import answer_stream

    repo_id, workspace = _resolve_ws(body)

    def _gen():  # type: ignore[no-untyped-def]
        for ev in answer_stream(
            query,
            mode=str(body.get("mode") or "mix"),
            repo_id=repo_id,
            workspace=workspace,
            top_k=max(1, min(int(body.get("top_k") or 8), 30)),
            websearch=bool(body.get("websearch")),
            use_cache=bool(body.get("use_cache", True)),
        ):
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.delete("/api/kg/query-cache")
def kg_clear_query_cache(request: Request):
    _require_admin(request)
    from app.services.knowledge.kg_query import clear_query_cache

    return ok({"cleared": clear_query_cache()})


# ---------------- 图谱 ----------------


@app.get("/api/kg/graph")
def kg_graph_route(request: Request, repo_id: int = 0, workspace: str = "", max_nodes: int = 500, search: str = "", focus: str = "", depth: int = 1):
    from app.services.knowledge.kg_query import kg_graph

    return ok(kg_graph(repo_id=repo_id, workspace=workspace, max_nodes=max(10, min(max_nodes, 2000)), search=search, focus=focus, depth=max(1, min(depth, 4))))


@app.get("/api/kg/entity/exists")
def kg_entity_exists(name: str, repo_id: int = 0, workspace: str = ""):
    from app.services.knowledge.kg_query import entity_exists

    return ok(entity_exists(name, repo_id=repo_id, workspace=workspace))


@app.patch("/api/kg/entity")
async def kg_edit_entity(request: Request, data: KgEntityEditIn):
    _require_admin(request)
    body = data.model_dump()
    name = (body.get("name") or "").strip()
    if not name:
        raise ApiError(400, "name 必填", 400)
    from app.services.knowledge.kg_merge import edit_entity, rename_entity

    repo_id, workspace = _resolve_ws(body)
    try:
        new_name = (body.get("new_name") or "").strip()
        if new_name and new_name != name:
            return ok(rename_entity(repo_id, workspace, name, new_name))
        return ok(edit_entity(repo_id, workspace, name, etype=str(body.get("etype") or ""), description=str(body.get("description") or "")))
    except LookupError as e:
        raise ApiError(404, str(e), 404)


@app.post("/api/kg/entity/merge")
async def kg_merge_entity(request: Request, data: KgEntityMergeIn):
    _require_admin(request)
    body = data.model_dump()
    into = (body.get("into") or "").strip()
    sources = [s for s in (body.get("sources") or []) if str(s).strip() and str(s).strip() != into]
    if not into or not sources:
        raise ApiError(400, "into 与 sources 必填", 400)
    from app.services.knowledge.kg_merge import merge_entities

    repo_id, workspace = _resolve_ws(body)
    try:
        return ok(merge_entities(repo_id, workspace, [str(s) for s in sources], into))
    except LookupError as e:
        raise ApiError(404, str(e), 404)


@app.delete("/api/kg/entity")
def kg_delete_entity(request: Request, name: str, repo_id: int = 0, workspace: str = ""):
    _require_admin(request)
    from app.services.knowledge.kg_merge import delete_entity

    try:
        return ok(delete_entity(repo_id, workspace, name))
    except LookupError as e:
        raise ApiError(404, str(e), 404)


@app.patch("/api/kg/relation")
async def kg_edit_relation(request: Request, data: KgRelationEditIn):
    _require_admin(request)
    body = data.model_dump()
    rel_id = int(body.get("id") or 0)
    if not rel_id:
        raise ApiError(400, "id 必填", 400)
    from app.services.knowledge.kg_merge import edit_relation

    repo_id, workspace = _resolve_ws(body)
    try:
        return ok(edit_relation(repo_id, workspace, rel_id, rtype=str(body.get("rtype") or ""), description=str(body.get("description") or "")))
    except LookupError as e:
        raise ApiError(404, str(e), 404)


@app.delete("/api/kg/relation")
def kg_delete_relation(request: Request, id: int, repo_id: int = 0, workspace: str = ""):
    _require_admin(request)
    from app.services.knowledge.kg_merge import delete_relation

    try:
        return ok(delete_relation(repo_id, workspace, id))
    except LookupError as e:
        raise ApiError(404, str(e), 404)


# ---------------- 社区 ----------------


@app.post("/api/kg/communities/build")
async def kg_build_communities(request: Request, data: KgCommunitiesBuildIn):
    _require_admin(request)
    body = data.model_dump()
    repo_id, workspace = _resolve_ws(body)
    from app.services.knowledge.kg_communities import build_communities

    res = build_communities(repo_id=repo_id, workspace=workspace, llm=bool(body.get("llm", True)))
    settings_set_dirty(workspace)
    return ok(res)


def settings_set_dirty(workspace: str) -> None:
    from app.services.knowledge.doc_pipeline import settings_set

    settings_set(f"communities_dirty:{workspace}", "0")


@app.get("/api/kg/communities")
def kg_list_communities(request: Request, repo_id: int = 0, workspace: str = "", level: int = 0):
    from app.services.knowledge.kg_communities import list_communities

    return ok(list_communities(repo_id=repo_id, workspace=workspace, level=level))


# ---------------- 导出 / 工作区 / 设置 ----------------


@app.get("/api/kg/export")
def kg_export_route(request: Request, what: str = "entities", fmt: str = "json", repo_id: int = 0, workspace: str = ""):
    from app.services.knowledge.kg_export import export

    filename, content, ctype = export(what=what, fmt=fmt, repo_id=repo_id, workspace=workspace)
    b64 = base64.b64encode(content).decode()
    return ok({"filename": filename, "content_type": ctype, "size": len(content), "content_b64": b64, "text": content.decode("utf-8", errors="replace") if ctype.startswith("text/") or ctype == "application/json" else ""})


@app.get("/api/kg/workspaces")
def kg_workspaces():
    """工作区清单：kg_doc 状态表 + rag_documents 工作区列 distinct。"""
    from sqlalchemy import text

    from app.services.knowledge.rag import _tables_ready

    out: set[str] = set()
    with get_session() as sess:
        for (ws,) in sess.query(KgEntity.workspace).distinct().all():
            if ws:
                out.add(ws)
        for (ws,) in sess.query(KgRelation.workspace).distinct().all():
            if ws:
                out.add(ws)
        if _tables_ready(sess):
            for (ws,) in sess.execute(text("SELECT DISTINCT workspace FROM rag_documents WHERE workspace <> ''")).all():
                out.add(str(ws))
    return ok(sorted(out))


@app.get("/api/kg/settings")
def kg_get_settings():
    from app.core import websearch as ws
    from app.services.knowledge import embedding as emb
    from app.services.knowledge import rerank as rr
    from app.services.knowledge.doc_pipeline import settings_all

    merged = settings_all()
    dirty_keys = [k for k in merged if k.startswith("communities_dirty:")]
    return ok(
        {
            "settings": {k: v for k, v in merged.items() if not k.startswith("communities_dirty:")},
            "dirty": any(merged.get(k) == "1" for k in dirty_keys),
            "embedding": {"backend": emb.backend(), "dim": emb.dim()},
            "rerank_enabled": rr.enabled(),
            "websearch_available": ws.available(),
        }
    )


@app.put("/api/kg/settings")
async def kg_put_settings(request: Request, data: KgSettingsIn):
    _require_admin(request)
    body = data.model_dump()
    allowed = {
        "chunk_strategy",
        "chunk_size",
        "chunk_overlap",
        "chunk_drop_references",
        "gleaning_rounds",
        "query_cache_enabled",
        "user_prompt_prefix",
        "websearch_enabled",
        "websearch_max_results",
    }
    from app.services.knowledge.doc_pipeline import settings_set

    changed: dict[str, str] = {}
    for k, v in (body.get("settings") or {}).items():
        if k in allowed:
            settings_set(k, str(v))
            changed[k] = str(v)
    return ok({"changed": changed})


# ---------------- Ollama 兼容端点（/ollama/*，模块内自校验） ----------------


def _ollama_auth(request: Request) -> None:
    from app.core.config import get_settings

    s = get_settings()
    if not s.ollama_compat:
        raise ApiError(404, "Ollama 兼容端点未启用", 404)
    if s.ollama_api_key:
        key = request.headers.get("x-api-key") or ""
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            key = key or auth[7:]
        if key != s.ollama_api_key:
            raise ApiError(401, "Ollama API Key 无效", 401)
    elif s.env == "prod":
        raise ApiError(403, "生产环境必须配置 OLLAMA_API_KEY 才能开放 Ollama 兼容端点", 403)


@app.get("/ollama/api/version")
def ollama_version(request: Request):
    from app.core.config import VERSION

    _ollama_auth(request)
    return {"version": VERSION}


@app.get("/ollama/api/tags")
def ollama_tags(request: Request):
    from app.core.config import get_settings

    _ollama_auth(request)
    s = get_settings()
    return {
        "models": [
            {"name": f"{s.ollama_model_name}:latest", "model": f"{s.ollama_model_name}:latest", "size": 0, "details": {"family": "testforge-kg"}}
        ]
    }


def _ollama_prompt(body: dict) -> str:
    """chat/generate 请求 → 用户问题（取最后一条 user 消息或 prompt 字段）。"""
    messages = body.get("messages") or []
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "user":
            return str(m.get("content") or "")
    return str(body.get("prompt") or "")


def _ollama_answer(request: Request, body: dict) -> str:
    """走完整 KG 管线回答（检索 → 生成）。"""
    from app.services.knowledge.kg_query import _ANSWER_SYSTEM, _render_context, kg_search
    from app.services.knowledge.llm import LLMError, chat_once

    query = _ollama_prompt(body)
    if not query.strip():
        return ""
    repo_id = int(body.get("repo_id") or 0)
    workspace = str(body.get("workspace") or "")
    mode = str(body.get("mode") or "mix")
    res = kg_search(query, mode=mode, repo_id=repo_id, workspace=workspace)
    from app.core.config import get_settings

    s = get_settings()
    if not s.llm_api_key:
        raise LLMError("LLM_API_KEY 未配置：Ollama 兼容层的生成路径不可用（检索端点 /api/kg/search 可用）")
    return chat_once(f"## 用户问题\n{query}\n\n## 知识上下文\n{_render_context(res, s.user_prompt_prefix)}", system=_ANSWER_SYSTEM, role="query")


@app.post("/ollama/api/chat")
async def ollama_chat(request: Request):
    _ollama_auth(request)
    body = await request.json()
    content = _ollama_answer(request, body)
    from datetime import datetime

    if body.get("stream"):
        def _gen():  # type: ignore[no-untyped-def]
            # 轻量流式：整段按行回放（Ollama 客户端兼容 ndjson）
            for line in content.splitlines() or [content]:
                yield json.dumps({"model": body.get("model", ""), "created_at": datetime.now().isoformat(), "message": {"role": "assistant", "content": line + "\n"}, "done": False}) + "\n"
            yield json.dumps({"model": body.get("model", ""), "created_at": datetime.now().isoformat(), "message": {"role": "assistant", "content": ""}, "done": True}) + "\n"

        return StreamingResponse(_gen(), media_type="application/x-ndjson")
    return {"model": body.get("model", ""), "created_at": datetime.now().isoformat(), "message": {"role": "assistant", "content": content}, "done": True, "total_duration": 0}


@app.post("/ollama/api/generate")
async def ollama_generate(request: Request):
    _ollama_auth(request)
    body = await request.json()
    content = _ollama_answer(request, body)
    from datetime import datetime

    return {"model": body.get("model", ""), "created_at": datetime.now().isoformat(), "response": content, "done": True}


@app.post("/ollama/api/embeddings")
async def ollama_embeddings(request: Request):
    _ollama_auth(request)
    body = await request.json()
    text = str(body.get("prompt") or "")
    from app.services.knowledge.embedding import embed

    return {"embedding": embed(text)}
