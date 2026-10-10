"""TestForge 后端主应用：REST/SSE 唯一入口（单体：领域模块进程内直调）。

认证：除 /api/health 与 /api/auth/login 外全部需要 Bearer token（admin 全权，viewer 只读）。
"""

import hmac
import json
import logging
import re
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Integer, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.envelope import ApiError, err, json_body, ok
from app.core.auth import parse_token, renewed_token
from app.core.config import VERSION, get_settings
from app.core.logutil import new_trace_id, set_trace_id, setup_logging
from app.crud import crud_cases, crud_repos, crud_wiki_chat_sessions, crud_wiki_pages
from app.db.session import get_session, init_db
from app.models import (
    Cases,
    Contracts,
    Defects,
    Generations,
    Jobs,
    Repos,
    Requirements,
    Runs,
    Users,
    WikiPages,
)
from app.schemas.cases import CaseReviewIn, CaseUpdateIn
from app.schemas.generations import GenerationBatchIn, GenerationCreateIn
from app.schemas.knowledge import KnowledgeDocBody, WikiAskBody, WikiChatSessionBody
from app.schemas.repos import RepoCreateIn

log = logging.getLogger("app")

SERVICE_LIST = [
    "repo-svc",
    "wiki-builder",
    "contract-registry",
    "req-svc",
    "testgen-svc",
    "runner-svc",
    "trace-svc",
]


def _service_pings() -> list[tuple[str, Any]]:
    """服务健康探测表：各服务平铺 API 的 ping（单体同进程，db_ok 共享同一数据库）。"""
    from app.services.contract import api as contract_api
    from app.services.repo import api as repo_api
    from app.services.req import api as req_api
    from app.services.runner import api as runner_api
    from app.services.testgen import api as testgen_api
    from app.services.trace import api as trace_api
    from app.services.wiki import api as wiki_api

    return [
        ("repo-svc", repo_api.ping),
        ("wiki-builder", wiki_api.ping),
        ("contract-registry", contract_api.ping),
        ("req-svc", req_api.ping),
        ("testgen-svc", testgen_api.ping),
        ("runner-svc", runner_api.ping),
        ("trace-svc", trace_api.ping),
    ]


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    setup_logging("app", get_settings().log_level)
    s = get_settings()
    if s.env == "prod":
        problems = s.validate_for_prod()
        if problems:
            for p in problems:
                log.error("生产安全检查未通过: %s", p)
            raise RuntimeError(f"ENV=prod 安全检查未通过（{len(problems)} 项），拒绝启动——详见上方日志")
        log.info("生产安全检查通过（env=prod）")
    init_db()
    log.info("backend v%s up on :%d（单体：服务进程内直调）", VERSION, get_settings().app_port)
    from app.api.jobs import recover_and_start

    workers = recover_and_start()
    log.info("job queue started with %d worker(s)", workers)
    from app.services.knowledge.doc_pipeline import recover_and_start as recover_doc_pipeline

    recovered_docs = recover_doc_pipeline()
    if recovered_docs:
        log.info("kg doc pipeline recovered %d in-flight document(s)", recovered_docs)
    yield


app = FastAPI(title="TestForge Gateway", version=VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Trace-Id"],
)


_USER_STATUS_CACHE: dict[str, tuple[bool, bool, float]] = {}  # username -> (disabled, must_change, ts)
_USER_STATUS_TTL_S = 60.0


def _user_status_cached(username: str) -> tuple[bool, bool]:
    """(停用, 待强制改密) 60s 进程内缓存：无状态 token 下以最小代价拦截异常账号。"""
    import time as _time

    hit = _USER_STATUS_CACHE.get(username)
    if hit is not None and _time.time() - hit[2] < _USER_STATUS_TTL_S:
        return hit[0], hit[1]
    with get_session() as sess:
        u = sess.query(Users).filter(Users.username == username).first()
        disabled = bool(u.disabled) if u is not None else True  # 用户已被删除 → 视为停用
        must_change = bool(u.must_change) if u is not None else False
    _USER_STATUS_CACHE[username] = (disabled, must_change, _time.time())
    return disabled, must_change


def _user_disabled_cached(username: str) -> bool:
    return _user_status_cached(username)[0]


@app.middleware("http")
async def auth_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Bearer token 认证：admin 全权，viewer 只读；/api/health 与 /api/auth/login 豁免。

    停用/已删除账号即时拒绝（60s 缓存窗口）；剩余寿命 <6h 的合法 token 自动续签
    （X-Renewed-Token 响应头，前端无感换新）。
    """
    path = request.url.path
    if path.startswith("/api") and path not in ("/api/health", "/api/auth/login") and request.method != "OPTIONS":
        token = ""
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        if not token:
            token = request.query_params.get("token", "")  # SSE(EventSource) 无法带header，走查询参数
        info = parse_token(token) if token else None
        if info is None:
            return err(401, "未登录或登录已过期", 401)
        disabled, must_change = _user_status_cached(info["username"])
        if disabled:
            return err(401, "账号已被停用，请联系管理员", 401)
        # 聊天放行所有认证用户（AI 写操作在工具层按角色二次拦截）；改密为自助操作
        self_service = path == "/api/auth/change-password" or path == "/api/assistant/chat"
        if must_change and not self_service:
            return err(403, "检测到默认口令，请先修改密码后再使用系统", 403)
        if not self_service and info["role"] != "admin" and request.method != "GET":
            return err(403, "只读账号无权执行该操作", 403)
        request.state.user = info
        fresh = renewed_token(info)
    else:
        fresh = None
    response = await call_next(request)
    if fresh:
        response.headers["X-Renewed-Token"] = fresh
    return response


@app.middleware("http")
async def trace_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    tid = request.headers.get("x-trace-id") or (new_trace_id() if request.method != "GET" else "-")
    set_trace_id(tid)
    t0 = time.time()
    response = await call_next(request)
    response.headers["X-Trace-Id"] = tid
    log.info("%s %s -> %d %.1fms trace=%s", request.method, request.url.path, response.status_code, (time.time() - t0) * 1000, tid)
    return response


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError):  # type: ignore[no-untyped-def]
    return err(exc.code, exc.message, exc.http_status)


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):  # type: ignore[no-untyped-def]
    log.exception("unhandled: %s", exc)
    return err(500, f"internal error: {exc}", 500)


# ---------------- 系统 ----------------


@app.get("/api/health")
def health():
    return ok({"status": "ok", "version": VERSION})


# ---------------- 认证 ----------------


@app.get("/api/system/services")
def system_services():
    """M0 验收点：服务链路状态（单体：进程内直调各服务 ping，db_ok 共享同一数据库）。"""
    items = []
    for name, ping_fn in _service_pings():
        try:
            res = ping_fn()
            items.append({"name": name, "ok": True, "db_ok": res.get("db_ok", False), "version": res.get("version", "")})
        except Exception as exc:  # noqa: BLE001
            items.append({"name": name, "ok": False, "db_ok": False, "error": str(exc)[:120]})
    return ok({"services": items, "all_green": all(i["ok"] and i["db_ok"] for i in items), "mode": "mono"})


# ---------------- 统计（仪表盘） ----------------


@app.get("/api/stats/summary")
def stats_summary():
    """仪表盘统计：标量计数合并为单条 SQL（子查询一次往返），分组计数各一条。"""
    with get_session() as sess:
        scalars = sess.execute(
            select(
                select(func.count()).select_from(Repos).scalar_subquery(),
                select(func.count()).select_from(Cases).where(Cases.status != "已替换").scalar_subquery(),
                select(func.count()).select_from(Cases).where(Cases.status == "待人审").scalar_subquery(),
                select(func.count()).select_from(Runs).scalar_subquery(),
                select(func.count()).select_from(Runs).where(Runs.status == "success").scalar_subquery(),
                select(func.count()).select_from(Requirements).scalar_subquery(),
                select(func.count()).select_from(Defects).where(Defects.status.notin_(["已关闭"])).scalar_subquery(),
                select(func.count()).select_from(WikiPages).scalar_subquery(),
                select(func.count()).select_from(WikiPages).where(WikiPages.stale.is_(True)).scalar_subquery(),
                select(func.count()).select_from(Contracts).scalar_subquery(),
                select(func.count())
                .select_from(Requirements)
                .where(Requirements.status.in_(["待人审", "规则冲突待确认"]))
                .scalar_subquery(),
                select(func.count()).select_from(Jobs).where(Jobs.status.in_(["queued", "running"])).scalar_subquery(),
                select(func.count()).select_from(Cases).where(Cases.stale.is_(True)).scalar_subquery(),
            )
        ).one()
        (repos, cases_total, pending, runs_total, runs_ok, reqs_total, defects_open, wiki_pages, wiki_stale, contracts, reqs_pending, jobs_active, cases_stale) = scalars
        return ok(
            {
                "repos": repos,
                "cases_total": cases_total,
                "cases_by_layer": _count_by(sess, Cases.layer),
                "cases_by_status": _count_by(sess, Cases.status),
                "pending_reviews": pending,
                "runs_total": runs_total,
                "runs_pass_rate": round(runs_ok / runs_total * 100, 1) if runs_total else 0.0,
                "requirements_total": reqs_total,
                "requirements_pending": reqs_pending,
                "jobs_active": jobs_active,
                "cases_stale": cases_stale,
                "defects_open": defects_open,
                "wiki_pages": wiki_pages,
                "wiki_stale": wiki_stale,
                "contracts": contracts,
            }
        )


def _count_by(sess, column) -> dict:  # type: ignore[no-untyped-def]
    rows = sess.query(column, func.count()).group_by(column).all()
    return {k or "": v for k, v in rows}


def _normalize_case_schema(schema_json: str) -> dict:
    """用例 schema 归一化：新老两种存储形状统一为顶层含 code_file 的对象。

    新形状：{"code_file": "<源码>", "code": ...}；
    老形状：{"code": ..., "schema_json": "<新形状的 JSON 字符串>"}（双层嵌套）。
    """
    try:
        obj = json.loads(schema_json) if schema_json else {}
    except json.JSONDecodeError:
        return {}
    if not isinstance(obj, dict):
        return {}
    if "code_file" not in obj and isinstance(obj.get("schema_json"), str):
        try:
            inner = json.loads(obj["schema_json"])
            if isinstance(inner, dict):
                code_file = inner.pop("code_file", "")
                obj = {**inner, "code_file": code_file or ""}
        except json.JSONDecodeError:
            pass
    return obj


# ---------------- Wiki（M2） ----------------


@app.get("/api/wiki")
def list_wiki(repo_id: int = 0, db: Session = Depends(get_db)):
    from app.models import WikiPages

    q = db.query(WikiPages)
    if repo_id:
        q = q.filter(WikiPages.repo_id == repo_id)
    rows = q.order_by(WikiPages.level, WikiPages.id).all()
    return ok(
        [
            {
                "id": w.id,
                "repo_id": w.repo_id,
                "level": w.level,
                "title": w.title,
                "module": w.module,
                "function": w.function,
                "rev": w.rev,
                "stale": w.stale,
                "updated_at": w.updated_at.isoformat(),
            }
            for w in rows
        ]
    )



@app.get("/api/wiki/health")
def wiki_health(db: Session = Depends(get_db)):
    from app.models import WikiPages

    rows = db.query(WikiPages.repo_id, func.count(), func.sum(func.cast(WikiPages.stale, Integer))).group_by(WikiPages.repo_id).all()
    return ok([{"repo_id": rid, "pages": total, "stale": int(stale or 0)} for rid, total, stale in rows])


@app.get("/api/wiki/lint")
def wiki_lint(repo_id: int, db: Session = Depends(get_db)):
    """Wiki 健康体检（OpenWiki lint 移植·确定性规则版）：stale/超短页/重复标题/孤儿页/模块覆盖缺口。"""
    from app.models import Functions, WikiPages
    from app.services.wiki.analytics import lint_repo, related_map

    rows = db.query(WikiPages).filter(WikiPages.repo_id == repo_id).all()
    modules = [m for (m,) in db.query(Functions.module).filter(Functions.repo_id == repo_id).distinct().all()]
    pages = [
        {"id": w.id, "title": w.title, "level": w.level, "module": w.module, "stale": w.stale, "len": len(w.content_md or "")}
        for w in rows
    ]
    related = related_map([{"id": w.id, "title": w.title, "text": f"{w.title}\n{(w.content_md or '')[:2000]}"} for w in rows])
    return ok(lint_repo(pages, modules, related))


@app.get("/api/wiki/graph")
def wiki_graph(repo_id: int = 0, db: Session = Depends(get_db)):
    """Wiki 互链图谱数据（OpenWiki WikiGraphView 移植的配套接口）：
    页面为节点，TF-IDF 余弦相似（related_map，阈值 0.05 / 每页 top-6）为边；
    问答存档的知识文档（meta.sources 非空）作为 doc 节点入图，与来源 wiki 页
    连 qa_reference 边（OpenWiki qa_reference 双向边对等物）。
    必须注册在 /api/wiki/{page_id} 之前，否则 "graph" 会被路径参数吞掉。"""
    from sqlalchemy import text as _text

    from app.models import WikiPages
    from app.services.wiki.analytics import related_map

    q = db.query(WikiPages)
    if repo_id:
        q = q.filter(WikiPages.repo_id == repo_id)
    rows = q.order_by(WikiPages.level, WikiPages.id).all()
    # 问答沉淀的知识文档（带来源页引用的才入图）
    doc_rows = db.execute(
        _text("SELECT doc_key, title, meta FROM rag_documents WHERE kind = 'user_doc'"),
        {},
    ).all()
    doc_nodes = []
    qa_edges = []
    for dk, dtitle, raw in doc_rows:
        try:
            meta = raw if isinstance(raw, dict) else json.loads(raw or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        refs = meta.get("sources") or []
        if not refs:
            continue
        if repo_id:
            # meta 里没记 repo 的旧数据按 doc_key 前缀放行，避免历史存档消失
            pass
        doc_nodes.append({"id": dk, "title": dtitle, "level": "doc", "module": "", "stale": False})
        for ref in refs:
            pid = int(ref.get("id") or 0)
            if pid > 0:
                qa_edges.append({"source": dk, "target": str(pid), "weight": 0.9, "relation": "qa_reference"})

    nodes = [
        {
            "id": str(w.id),
            "title": w.title,
            "level": w.level,
            "module": w.module or "",
            "stale": bool(w.stale),
        }
        for w in rows
    ]
    related = related_map(
        [{"id": w.id, "title": w.title, "text": f"{w.title}\n{(w.content_md or '')[:2000]}"} for w in rows]
    )
    seen: set[tuple[int, int]] = set()
    edges = []
    for pid, neighbors in related.items():
        for n in neighbors:
            key = (min(pid, n["id"]), max(pid, n["id"]))
            if key in seen:
                continue
            seen.add(key)
            edges.append({"source": str(pid), "target": str(n["id"]), "weight": n["score"]})
    edges.extend(qa_edges)
    return ok({"nodes": nodes + doc_nodes, "edges": edges})


# ---------------- 知识文档（用户注入知识 → RAG 双索引 → 生成上下文） ----------------
# 闭环：Wiki 页上传 → rag_documents(kind='user_doc') → testgen context 检索 kinds 含 'user_doc'
# → 生成引用。不混 kind='wiki'（wiki 重建 replace_scope 会整批清掉）。


@app.get("/api/knowledge/documents")
def list_knowledge_documents(repo_id: int = 0, db: Session = Depends(get_db)):
    from sqlalchemy import text

    from app.services.knowledge.rag import ensure_rag_documents_table

    ensure_rag_documents_table()
    sql = "SELECT doc_key, repo_id, title, LENGTH(content) AS chars, meta, updated_at FROM rag_documents WHERE kind = 'user_doc'"
    params: dict = {}
    if repo_id:
        sql += " AND repo_id = :rid"
        params["rid"] = repo_id
    sql += " ORDER BY updated_at DESC LIMIT 200"
    rows = db.execute(text(sql), params).all()

    def _meta_of(raw) -> dict:  # type: ignore[no-untyped-def]
        if isinstance(raw, dict):
            return raw
        try:
            return json.loads(raw or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}

    return ok(
        [
            {
                "doc_key": r[0],
                "repo_id": r[1],
                "title": r[2],
                "chars": int(r[3] or 0),
                "updated_at": r[5].isoformat() if r[5] else None,
                "source": _meta_of(r[4]).get("source", ""),
                "source_url": _meta_of(r[4]).get("source_url", ""),
                "sources_count": len(_meta_of(r[4]).get("sources") or []),
            }
            for r in rows
        ]
    )


def _assess_knowledge_content(title: str, content: str) -> tuple[float, str]:
    """评估门卫（OpenWiki wiki_engine.rs assess_content 移植）：知识分 <0.5 拒绝入库。
    启发式兜底 + LLM 打分；LLM 不可用时放行（门卫不能挡住正常上传）。"""
    if len(content) < 30:
        return 0.0, "内容过短（<30 字），无检索价值"
    try:
        from app.services.knowledge.llm import chat_once

        prompt = (
            "评估以下知识内容对『AI 生成测试用例』的价值，返回 JSON："
            '{"score": 0到1的小数, "reason": "一句话理由"}。\n'
            "高分(>=0.7)：业务规则/接口约定/验收标准/领域知识/代码文档；"
            "低分(<0.5)：无意义碎片、纯闲聊、与软件测试无关的噪音。\n\n"
            f"【标题】{title}\n【内容】{content[:2000]}"
        )
        raw = chat_once(prompt, system="只输出 JSON，不要解释。", role="keyword").strip()
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
        m = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(m.group(0) if m else raw)
        return float(data.get("score", 1.0)), str(data.get("reason", ""))
    except Exception as exc:  # noqa: BLE001
        log.warning("knowledge assess 放行（评估服务不可用）: %s", exc)
        return 1.0, "评估服务不可用，放行"


@app.post("/api/knowledge/documents/create")
def create_knowledge_document(body: KnowledgeDocBody):
    """用户知识文档入库（Wiki 页上传卡片）：upsert 进 rag_documents（向量+全文双索引），
    AI 生成用例的六路上下文会自动检索引用。doc_key=repo+标题哈希，同标题重复上传是更新。

    三道服务端门卫（OpenWiki 移植）：①敏感信息扫描（sensitive_filter.rs）
    ②问答存档反幻觉——kind_hint=qa 且无来源页时 422（save_message_as_page 的
    ai_only 禁入库）③评估门卫——知识分 <0.5 拒绝（assess_content）。"""
    import hashlib

    from app.core.sensitive import scan_sensitive
    from app.services.knowledge.docstatus import mark
    from app.services.knowledge.rag import ensure_rag_documents_table, index_document

    title = body.title.strip()
    content = body.content.strip()
    if not title or not content:
        raise ApiError(400, "title 与 content 必填", 400)

    # 门卫①：敏感信息（写入侧拦截，防密钥进库被全员检索到）
    hits = scan_sensitive(f"{title}\n{content}")
    if hits:
        raise ApiError(422, f"内容命中敏感信息（{'、'.join(hits)}），已拒绝入库——请脱敏后重试", 422)

    # 门卫②：问答存档必须有知识库来源（ai_only 禁止污染知识库）
    clean_sources = [
        {"id": int(s.get("id") or 0), "title": str(s.get("title") or "")}
        for s in (body.sources or [])
        if int(s.get("id") or 0) > 0
    ]
    if body.kind_hint == "qa" and not clean_sources:
        raise ApiError(422, "该回答没有知识库来源支撑，禁止存入知识库（反幻觉门卫）", 422)

    # 门卫③：评估知识分（qa 存档已过来源门卫，跳过重复评估）
    if body.assess and body.kind_hint != "qa":
        score, reason = _assess_knowledge_content(title, content)
        if score < 0.5:
            raise ApiError(422, f"评估门卫：知识分 {score:.2f} < 0.5，拒绝入库——{reason}", 422)

    ensure_rag_documents_table()
    doc_key = "userdoc:" + hashlib.sha1(f"{body.repo_id}:{title}".encode()).hexdigest()[:16]
    meta: dict = {"source": "qa-archive" if body.kind_hint == "qa" else "wiki-page-upload"}
    if body.kind_hint == "qa":
        meta["sources"] = clean_sources  # qa_reference 边数据源（图谱可见）
    index_document(doc_key, "user_doc", title, content, repo_id=body.repo_id, meta=meta)
    mark(body.repo_id, "user_doc", doc_key)
    return ok({"doc_key": doc_key, "indexed": True})


@app.post("/api/knowledge/documents/delete")
def delete_knowledge_document(doc_key: str):
    from app.services.knowledge.rag import remove_document

    remove_document(doc_key)
    return ok({"deleted": doc_key})


@app.post("/api/wiki/lint/fix-duplicates")
def wiki_fix_duplicates(repo_id: int):
    """处置重复标题（OpenWiki lint 处置动作移植）：同标题仅保留最早一页，其余删除
    （连同其 rag_documents 检索行），消除检索稀释。返回删除清单。"""

    from sqlalchemy import text

    from app.models import WikiPages
    from app.services.knowledge.rag import remove_document

    with get_session() as sess:
        rows = sess.query(WikiPages).filter(WikiPages.repo_id == repo_id).order_by(WikiPages.id).all()
        seen: dict[str, int] = {}
        removed: list[int] = []
        for w in rows:
            key = w.title.strip()
            if key in seen:
                removed.append(w.id)
                sess.delete(w)
            else:
                seen[key] = w.id
        if removed:
            # 先清双向依赖边（wiki_deps.page_id 与 depends_on_page_id 都外键指向 wiki_pages），
            # 否则删页直接 FK violation——边随被删页一起消失，保留页不受影响
            sess.execute(
                text(
                    "DELETE FROM wiki_deps WHERE page_id = ANY(:ids) OR depends_on_page_id = ANY(:ids)"
                ),
                {"ids": removed},
            )
            sess.commit()
    for pid in removed:
        remove_document(f"wiki:{pid}")
    return ok({"removed": removed, "kept": len(seen)})


@app.get("/api/knowledge/search")
def knowledge_search(q: str, repo_id: int = 0, limit: int = 8, hide_sensitive: bool = False):
    """检索测试台：测试资料全类目透明预演——需求文档(req)/技术文档(wiki+user_doc)/
    测试计划(plan)/测试用例(case)/功能缺陷(defect)+缺陷教训(lesson) 七路混合检索。
    hide_sensitive=1 时命中片段打码（OpenWiki sensitive_filter 查询侧对等物）。"""
    from app.core.sensitive import redact_sensitive
    from app.services.knowledge.rag import hybrid_search

    q = q.strip()
    if not q:
        raise ApiError(400, "q 必填", 400)
    hits = hybrid_search(
        q,
        kinds=("req", "plan", "case", "defect", "user_doc", "lesson", "wiki"),
        limit=max(1, min(limit, 20)),
        repo_id=repo_id or 0,
    )
    return ok(
        [
            {
                "doc_key": h["doc_key"],
                "kind": h["kind"],
                "title": h["title"],
                "excerpt": redact_sensitive(h["content"][:180]) if hide_sensitive else h["content"][:180],
                "score": round(h.get("score", 0.0), 4),
                "repo_id": h.get("repo_id", 0),
            }
            for h in hits
        ]
    )


@app.get("/api/wiki/{page_id}/related")
def wiki_related(page_id: int, limit: int = 6, db: Session = Depends(get_db)):
    """相关页面（OpenWiki TF-IDF 互链移植）：同仓库页面按余弦相似度 top-K 推荐。"""
    from app.models import WikiPages
    from app.services.wiki.analytics import related_map

    w = crud_wiki_pages.get(db, page_id)
    if w is None:
        raise ApiError(404, "页面不存在", 404)
    rows = db.query(WikiPages).filter(WikiPages.repo_id == w.repo_id).all()
    # 自身必须参与互链计算，再从结果里剔除，否则永远查不到邻居
    related = related_map(
        [{"id": x.id, "title": x.title, "text": f"{x.title}\n{(x.content_md or '')[:2000]}"} for x in rows],
        top_k=limit + 1,
    )
    return ok(
        [
            {"id": n["id"], "title": n["title"], "score": n["score"]}
            for n in related.get(page_id, [])
            if n["id"] != page_id
        ][:limit]
    )


@app.post("/api/wiki/ask")
def wiki_ask_route(body: WikiAskBody):
    """Wiki 问答（OpenWiki 三阶段 RAG 移植）：指代解析+时间过滤 → 检索 → LLM 作答 → 来源。

    session_id 非空时：user/assistant 消息落库（来源页存 sources_json，
    供追问指代解析取上一轮来源——原版 resolve_follow_up_page_ids 从消息元数据取），
    首条问题自动作会话标题。"""
    from app.models import WikiChatMessage, WikiChatSession
    from app.services.wiki.ask import ask_wiki

    question = body.question.strip()
    if not question:
        raise ApiError(400, "question 必填", 400)

    last_sources: list[dict] = []
    if body.session_id > 0:
        with get_session() as sess:
            prev = (
                sess.query(WikiChatMessage)
                .filter(WikiChatMessage.session_id == body.session_id, WikiChatMessage.role == "assistant")
                .order_by(WikiChatMessage.id.desc())
                .first()
            )
            if prev is not None:
                try:
                    last_sources = json.loads(prev.sources_json or "[]")
                except json.JSONDecodeError:
                    last_sources = []

    result = ask_wiki(body.repo_id, question, history=body.history, last_sources=last_sources)

    if body.session_id > 0:
        with get_session() as sess:
            sess.add(WikiChatMessage(session_id=body.session_id, role="user", content=question))
            sess.add(
                WikiChatMessage(
                    session_id=body.session_id,
                    role="assistant",
                    content=result["answer"],
                    sources_json=json.dumps(result.get("sources") or [], ensure_ascii=False),
                    source_mode=result.get("source_mode", "knowledge_base"),
                )
            )
            sess.query(WikiChatSession).filter(WikiChatSession.id == body.session_id).update({"updated_at": func.now()})
            first = (
                sess.query(WikiChatMessage)
                .filter(WikiChatMessage.session_id == body.session_id, WikiChatMessage.role == "user")
                .count()
            )
            if first <= 2:  # 本轮 user 消息是首问 → 自动作会话标题
                sess.query(WikiChatSession).filter(WikiChatSession.id == body.session_id).update(
                    {"title": question[:40]}
                )
            sess.commit()
    return ok(result)


# ---------------- Wiki 问答多会话（OpenWiki wiki_chat_sessions 移植） ----------------


@app.get("/api/wiki/chat/sessions")
def wiki_chat_sessions(repo_id: int = 0, db: Session = Depends(get_db)):
    from app.models import WikiChatMessage, WikiChatSession

    q = db.query(WikiChatSession)
    if repo_id:
        q = q.filter(WikiChatSession.repo_id == repo_id)
    rows = q.order_by(WikiChatSession.updated_at.desc()).limit(50).all()
    counts: dict[int, int] = {
        sid: cnt for sid, cnt in db.query(WikiChatMessage.session_id, func.count()).group_by(WikiChatMessage.session_id).all()
    }
    return ok(
        [
            {
                "id": s.id,
                "repo_id": s.repo_id,
                "title": s.title,
                "messages": int(counts.get(s.id, 0)),
                "updated_at": s.updated_at.isoformat(),
            }
            for s in rows
        ]
    )


@app.post("/api/wiki/chat/sessions/create")
def create_wiki_chat_session(body: WikiChatSessionBody, db: Session = Depends(get_db)):
    s = crud_wiki_chat_sessions.create(db, obj_in={"repo_id": body.repo_id, "title": body.title.strip() or "新问答"})
    return ok({"id": s.id, "title": s.title})


@app.delete("/api/wiki/chat/sessions/{session_id}")
def delete_wiki_chat_session(session_id: int):
    from app.models import WikiChatMessage, WikiChatSession

    with get_session() as sess:
        sess.query(WikiChatMessage).filter(WikiChatMessage.session_id == session_id).delete()
        n = sess.query(WikiChatSession).filter(WikiChatSession.id == session_id).delete()
        sess.commit()
    if not n:
        raise ApiError(404, "会话不存在", 404)
    return ok({"deleted": session_id})


@app.get("/api/wiki/chat/sessions/{session_id}/messages")
def wiki_chat_messages(session_id: int, db: Session = Depends(get_db)):
    from app.models import WikiChatMessage

    rows = (
        db.query(WikiChatMessage)
        .filter(WikiChatMessage.session_id == session_id)
        .order_by(WikiChatMessage.id)
        .limit(200)
        .all()
    )
    return ok(
        [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "sources": json.loads(m.sources_json or "[]"),
                "source_mode": m.source_mode,
                "created_at": m.created_at.isoformat(),
            }
            for m in rows
        ]
    )


# ---------------- 知识洞察报告（OpenWiki 注意力雷达的对等物） ----------------
# 雷达分析"你最近在关注什么"；知识洞察分析"知识库缺什么/哪里会误导生成"——
# 统计层确定性计算（页/文档/问答/体检），LLM 只做归纳，失败时降级返回纯统计。

_INSIGHTS_CACHE: dict[int, tuple[float, dict]] = {}
_INSIGHTS_TTL = 300.0


@app.get("/api/wiki/insights")
def wiki_insights(repo_id: int, db: Session = Depends(get_db)):
    """知识洞察（OpenWiki attention 雷达移植·知识库版）：板块化报告——
    总览 / 知识缺口（无来源问答=检索不中的真实问题）/ 过期风险 / 关注焦点 / 行动建议。"""
    import time as _time

    from app.models import WikiChatMessage, WikiChatSession, WikiPages
    from app.services.wiki.analytics import lint_repo, related_map

    cached = _INSIGHTS_CACHE.get(repo_id)
    if cached and _time.time() - cached[0] < _INSIGHTS_TTL:
        return ok(cached[1])

    pages = db.query(WikiPages).filter(WikiPages.repo_id == repo_id).all()
    modules = [m for (m,) in db.query(WikiPages.module).filter(WikiPages.repo_id == repo_id).distinct().all()]
    sessions = db.query(WikiChatSession).filter(WikiChatSession.repo_id == repo_id).all()
    session_ids = [s.id for s in sessions]
    msgs = (
        db.query(WikiChatMessage)
        .filter(WikiChatMessage.session_id.in_(session_ids), WikiChatMessage.role == "assistant")
        .all()
        if session_ids
        else []
    )
    from sqlalchemy import text as _text

    doc_count = db.execute(
        _text("SELECT count(*) FROM rag_documents WHERE kind='user_doc' AND repo_id=:r"), {"r": repo_id}
    ).scalar() or 0

    related = related_map(
        [{"id": w.id, "title": w.title, "text": f"{w.title}\n{(w.content_md or '')[:2000]}"} for w in pages]
    )
    lint = lint_repo(
        [
            {"id": w.id, "title": w.title, "level": w.level, "module": w.module, "stale": w.stale, "len": len(w.content_md or "")}
            for w in pages
        ],
        [m for m in modules if m],
        related,
    )

    # 知识缺口：no_data 问答 = 用户问了、知识库答不上 → 检索盲区的真实信号
    gaps = [m.content for m in msgs if m.source_mode == "no_data"][-10:]
    focus_questions = [m.content for m in msgs if m.source_mode == "knowledge_base"][-10:]
    level_dist: dict[str, int] = {}
    for w in pages:
        level_dist[w.level] = level_dist.get(w.level, 0) + 1

    stats = {
        "repo_id": repo_id,
        "pages": len(pages),
        "stale": sum(1 for w in pages if w.stale),
        "level_dist": level_dist,
        "doc_count": int(doc_count),
        "qa_sessions": len(sessions),
        "qa_answers": len(msgs),
        "qa_no_data": len(gaps),
        "lint_findings": len(lint["findings"]),
        "lint_top": [f["title"] for f in lint["findings"][:5]],
        "gap_questions": gaps,
        "recent_questions": focus_questions,
    }

    report: dict = {"stats": stats, "insights": None, "insights_error": ""}
    if stats["pages"] or stats["qa_answers"]:
        try:
            from app.services.knowledge.llm import chat_once

            prompt = (
                "你是知识库运营分析师。依据以下 TestForge 知识库统计，输出 JSON 板块（全部中文）：\n"
                '{"summary": "两句话总览", "coverage_gaps": ["知识盲区，每条一句话"],'
                ' "stale_risks": ["过期/质量风险"], "focus_topics": ["用户关注焦点"],'
                ' "actions": ["可执行建议，按优先级"]}\n'
                "注意：qa_no_data 问题代表检索盲区（用户问了但知识库答不上），是最重要的缺口信号。\n\n"
                f"【统计】{json.dumps(stats, ensure_ascii=False)}"
            )
            raw = chat_once(prompt, system="只输出 JSON。", role="query").strip()
            raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
            m = re.search(r"\{.*\}", raw, re.S)
            report["insights"] = json.loads(m.group(0) if m else raw)
        except Exception as exc:  # noqa: BLE001
            report["insights_error"] = f"LLM 洞察生成失败（统计仍可用）：{exc}"

    _INSIGHTS_CACHE[repo_id] = (_time.time(), report)
    return ok(report)


@app.get("/api/wiki/{page_id}")
def get_wiki_page(page_id: int, db: Session = Depends(get_db)):
    w = crud_wiki_pages.get(db, page_id)
    if w is None:
        raise ApiError(404, "页面不存在", 404)
    return ok(
        {
            "id": w.id,
            "repo_id": w.repo_id,
            "level": w.level,
            "title": w.title,
            "content_md": w.content_md,
            "rev": w.rev,
            "stale": w.stale,
        }
    )


# ---------------- Wiki 编译锁（OpenWiki acquire_compile_lock 移植） ----------------
# 手动重建 / pull / webhook 后台重建并发时会重复跑 gRPC 重建 + 双写 rag 索引；
# per-repo 互斥锁，非阻塞获取，拿不到直接报 409 / 跳过。

_WIKI_REBUILD_LOCKS: dict[int, threading.Lock] = {}
_WIKI_REBUILD_LOCKS_GUARD = threading.Lock()


def _wiki_rebuild_lock(repo_id: int) -> threading.Lock:
    with _WIKI_REBUILD_LOCKS_GUARD:
        lock = _WIKI_REBUILD_LOCKS.get(repo_id)
        if lock is None:
            lock = threading.Lock()
            _WIKI_REBUILD_LOCKS[repo_id] = lock
        return lock


@app.post("/api/wiki/rebuild")
async def rebuild_wiki(request: Request):
    """一键重建：body {repo_id, full?}。full=true 全量重编译并清 stale。
    同仓库已有重建在进行时返回 409（编译锁）。"""

    body = await json_body(request)
    repo_id = int(body.get("repo_id") or 0)
    full = bool(body.get("full"))
    with get_session() as sess:
        repo = sess.get(Repos, repo_id)
        if repo is None:
            raise ApiError(404, "repo 不存在", 404)
    lock = _wiki_rebuild_lock(repo_id)
    if not lock.acquire(blocking=False):
        raise ApiError(409, "该仓库 Wiki 正在重建中，请稍候（编译锁）", 409)
    try:
        from app.services.wiki.api import rebuild as wiki_rebuild

        res = wiki_rebuild(
            repo_id=repo_id,
            from_rev="" if full else str(repo.head_rev or ""),
            to_rev=str(repo.head_rev or ""),
            changed_files=[] if full else ["__stale__"],
        )
    finally:
        lock.release()
    return ok(res)


# ---------------- 仓库（M1 全量；M0 落库+列表） ----------------


@app.get("/api/repos")
def list_repos(db: Session = Depends(get_db)):
    rows = db.query(Repos).order_by(Repos.id.desc()).all()
    return ok(
        [
            {
                "id": r.id,
                "url": r.url,
                "branch": r.branch,
                "credential_ref": r.credential_ref,
                "status": r.status,
                "last_pull": r.last_pull.isoformat() if r.last_pull else None,
                "head_rev": r.head_rev,
            }
            for r in rows
        ]
    )


@app.post("/api/repos/create")
async def create_repo(data: RepoCreateIn):
    from app.core.config import get_settings as _gs
    from app.services.repo.gitops import GitError, validate_remote_url

    body = data.model_dump()
    url = (body.get("url") or "").strip()
    if not url:
        raise ApiError(1001, "url 必填")
    try:
        validate_remote_url(url, allow_local=_gs().allow_local_repo_url)
    except GitError as exc:
        raise ApiError(1003, str(exc), 400) from exc
    from app.services.repo.api import register as repo_register

    res = repo_register(
        url=url,
        branch=body.get("branch") or "main",
        credential_ref=body.get("credential_ref") or "",
        webhook=bool(body.get("webhook")),
    )
    return ok(res)


@app.post("/api/repos/{repo_id}/pull")
def pull_repo(repo_id: int):
    """拉取 + 增量索引 + 变更驱动回归 + 自动重建 Wiki（GitNexus staleness 闭环）。

    源码变更的函数 → 关联用例标 stale → 自动回归；同时后台重建受影响的 Wiki 页，
    知识层自动追平代码，不再依赖人工点「重建」。
    """
    from app.api.regression import regress_changed
    from app.services.repo.api import pull as repo_pull

    try:
        res = repo_pull(repo_id)
    except KeyError as exc:
        raise ApiError(404, f"repo {repo_id} 不存在", 404) from exc
    summary = regress_changed(repo_id, res.get("changed_functions") or [])
    if res.get("changed_functions"):
        _spawn_wiki_rebuild(repo_id, "pull")
    return ok({**res, "regression": summary, "wiki_rebuild": "triggered" if res.get("changed_functions") else "skipped"})


def _spawn_wiki_rebuild(repo_id: int, source: str) -> None:
    """后台重建受影响 Wiki 页（仅 stale 页），失败不阻塞调用方。
    编译锁被占（如手动重建进行中）时跳过本次，不排队堆积。"""
    from app.core.trace import emit

    tid = new_trace_id()
    set_trace_id(tid)

    def _run() -> None:
        lock = _wiki_rebuild_lock(repo_id)
        if not lock.acquire(blocking=False):
            emit("wiki", source, f"{source} 触发 Wiki 重建跳过 repo={repo_id}：已有重建在进行（编译锁）", trace_id=tid)
            set_trace_id("-")
            return
        try:
            with get_session() as sess:
                repo = sess.get(Repos, repo_id)
                head = str(repo.head_rev or "") if repo else ""
            from app.services.wiki.api import rebuild as wiki_rebuild

            r = wiki_rebuild(repo_id=repo_id, from_rev=head, to_rev=head, changed_files=["__stale__"])
            emit("wiki", source, f"{source} 触发 Wiki 增量重建 repo={repo_id}: {r}", trace_id=tid)
        except Exception as exc:  # noqa: BLE001
            emit("wiki", source, f"{source} 触发 Wiki 重建失败 repo={repo_id}: {exc}", trace_id=tid)
        finally:
            lock.release()
            set_trace_id("-")

    threading.Thread(target=_run, daemon=True).start()


@app.post("/api/repos/{repo_id}/webhook")
async def repo_webhook(repo_id: int, request: Request):
    """git webhook 接收端：后台拉取+变更回归，立即返回（webhook 要求快速 2xx）。

    settings.webhook_secret 非空时校验请求头 X-Webhook-Secret。
    """
    if get_settings().webhook_secret:
        header = request.headers.get("x-webhook-secret", "")
        if not hmac.compare_digest(header, get_settings().webhook_secret):
            raise ApiError(403, "webhook 密钥不符", 403)
    with get_session() as sess:
        repo = sess.get(Repos, repo_id)
        if repo is None:
            raise ApiError(404, "repo 不存在", 404)

    def _pull_and_regress() -> None:
        from app.api.regression import regress_changed
        from app.core.trace import emit
        from app.services.repo.api import pull as repo_pull

        tid = new_trace_id()
        set_trace_id(tid)
        try:
            res = repo_pull(repo_id)
            summary = regress_changed(repo_id, res.get("changed_functions") or [])
            emit(
                "仓库",
                "webhook",
                f"webhook 拉取完成 repo={repo_id} rev={str(res.get('head_rev') or '')[:8]} 变更函数={len(res.get('changed_functions') or [])} 回归={summary}",
                trace_id=tid,
            )
            if res.get("changed_functions"):
                _spawn_wiki_rebuild(repo_id, "webhook")
        except Exception as exc:  # noqa: BLE001
            emit("仓库", "webhook", f"webhook 拉取失败 repo={repo_id}: {exc}", trace_id=tid)
        finally:
            set_trace_id("-")

    threading.Thread(target=_pull_and_regress, daemon=True).start()
    return ok({"accepted": True, "repo_id": repo_id})


# ---------------- 函数（M1 生成目标） ----------------


@app.get("/api/functions")
def list_functions(repo_id: int = 0, module: str = "", name: str = "", db: Session = Depends(get_db)):
    from app.models import Functions

    q = db.query(Functions)
    if repo_id:
        q = q.filter(Functions.repo_id == repo_id)
    if module:
        q = q.filter(Functions.module.contains(module))
    if name:
        q = q.filter(Functions.name.contains(name))
    rows = q.order_by(Functions.id).limit(500).all()
    return ok(
        [
            {
                "id": f.id,
                "repo_id": f.repo_id,
                "module": f.module,
                "name": f.name,
                "signature": f.signature,
                "file": f.file,
                "line": f.line,
                "language": f.language or "python",
                "docstring": (f.docstring or "")[:200],
            }
            for f in rows
        ]
    )


# ---------------- 生成（M1 核心） ----------------


@app.post("/api/generations")
async def create_generation(data: GenerationCreateIn):
    from app.api.generations import new_generation

    body = data.model_dump()
    return ok(new_generation(body))


@app.post("/api/generations/batch")
async def create_generation_batch(data: GenerationBatchIn):
    """批量生成：body {repo_id, module?|names?[], layer?}。按模块圈选或显式函数名，逐个入队。"""
    from app.api.generations import new_batch

    body = data.model_dump()
    return ok(new_batch(body))


@app.get("/api/jobs")
def list_jobs(status: str = "", limit: int = 50, db: Session = Depends(get_db)):
    """任务队列台账（生成/回归），新→旧。"""
    q = db.query(Jobs)
    if status:
        q = q.filter(Jobs.status == status)
    rows = q.order_by(Jobs.id.desc()).limit(min(limit, 200)).all()
    return ok(
        [
            {
                "code": j.code,
                "kind": j.kind,
                "status": j.status,
                "gen_code": j.gen_code,
                "error": j.error,
                "created_at": j.created_at.isoformat(),
                "updated_at": j.updated_at.isoformat(),
            }
            for j in rows
        ]
    )


@app.get("/api/jobs/{job_code}")
def get_job(job_code: str, db: Session = Depends(get_db)):
    j = db.query(Jobs).filter(Jobs.code == job_code).first()
    if j is None:
        raise ApiError(404, "job 不存在", 404)
    return ok(
        {
            "code": j.code,
            "kind": j.kind,
            "status": j.status,
            "gen_code": j.gen_code,
            "payload": json.loads(j.payload_json or "{}"),
            "error": j.error,
            "created_at": j.created_at.isoformat(),
            "updated_at": j.updated_at.isoformat(),
        }
    )


@app.get("/api/generations/{gen_code}/export")
def export_generation(gen_code: str, db: Session = Depends(get_db)):
    """导出生成的测试文件（前端触发下载）。"""
    g = db.query(Generations).filter(Generations.code == gen_code).first()
    if g is None or not g.codegen:
        raise ApiError(404, "生成不存在或无代码产物", 404)
    src = g.codegen
    filename = f"test_tf_gen_{gen_code.lower()}.py"
    return ok({"filename": filename, "content": src, "lines": src.count("\n") + 1})


@app.post("/api/generations/{gen_code}/export-to-repo")
def export_generation_to_repo(gen_code: str, db: Session = Depends(get_db)):
    """把生成的测试文件真实写入仓库检出的 tests/testforge_generated/ 目录。"""
    g = db.query(Generations).filter(Generations.code == gen_code).first()
    if g is None or not g.codegen:
        raise ApiError(404, "生成不存在或无代码产物", 404)
    src = g.codegen
    repo = crud_repos.get(db, g.repo_id) if g.repo_id else None
    if repo is None or not repo.local_path:
        raise ApiError(404, "生成未绑定可用仓库检出", 404)
    dest_dir = Path(repo.local_path) / "tests" / "testforge_generated"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"test_tf_gen_{gen_code.lower()}.py"
    dest.write_text(src, encoding="utf-8")
    return ok({"path": str(dest), "filename": dest.name, "lines": src.count("\n") + 1})


@app.get("/api/generations/{gen_code}")
def get_generation(gen_code: str, db: Session = Depends(get_db)):
    from app.models import GenerationEvents

    g = db.query(Generations).filter(Generations.code == gen_code).first()
    if g is None:
        raise ApiError(404, "generation 不存在", 404)
    events = db.query(GenerationEvents).filter(GenerationEvents.gen_code == gen_code).order_by(GenerationEvents.id).all()
    return ok(
        {
            "generation_id": g.code,
            "status": g.status,
            "target": g.target_function,
            "layer": g.layer,
            "trace_id": g.trace_id,
            "events": [
                {"kind": e.kind, "stage": e.stage, "message": e.message, "progress": e.progress, "payload_json": e.payload_json}
                for e in events
            ],
        }
    )


@app.get("/api/generations/{gen_code}/events")
def generation_events(gen_code: str):
    from fastapi.responses import StreamingResponse

    from app.api.generations import stream_events

    return StreamingResponse(stream_events(gen_code), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------- 用例库（M1） ----------------


@app.get("/api/cases")
def list_cases(
    layer: str = "",
    module: str = "",
    status: str = "",
    category: str = "",
    source_req: str = "",
    stale: str = "",
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
):
    """用例库（服务端分页：page/page_size，total 为全量计数）。stale=true 只看待回归。"""
    from app.models import Cases

    q = db.query(Cases)
    if layer:
        q = q.filter(Cases.layer == layer)
    if module:
        q = q.filter(Cases.module.contains(module))
    if status:
        q = q.filter(Cases.status == status)
    else:
        q = q.filter(Cases.status != "已替换")  # 默认隐藏被新版本替换的历史用例
    if category:
        q = q.filter(Cases.category == category)
    if source_req:
        q = q.filter(Cases.source_req == source_req)
    if stale == "true":
        q = q.filter(Cases.stale.is_(True))
    total = q.count()
    page = max(1, page)
    page_size = min(max(1, page_size), 200)
    rows = q.order_by(Cases.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    by_layer = {lay: 0 for lay in ("ut", "api", "fn", "e2e", "contract")}
    by_layer.update(
        {k: v for k, v in db.query(Cases.layer, func.count()).filter(Cases.status != "已替换").group_by(Cases.layer).all()}
    )
    return ok(
        {
            "total": total,
            "page": page,
            "page_size": page_size,
            "stale_total": db.query(Cases).filter(Cases.stale.is_(True)).count(),
            "by_layer": by_layer,
            "by_category": _count_by(db, Cases.category),
            "items": [
                {
                    "id": c.id,
                    "code": c.code,
                    "layer": c.layer,
                    "title": c.title,
                    "module": c.module,
                    "category": c.category,
                    "status": c.status,
                    "source_req": c.source_req,
                    "confidence": c.confidence,
                    "trace_id": c.trace_id,
                    "gen_id": c.gen_id,
                    "target_function": c.target_function,
                    "last_run_ok": c.last_run_ok,
                    "stale": bool(c.stale),
                    "schema": _normalize_case_schema(c.schema_json),
                }
                for c in rows
            ],
        }
    )


@app.get("/api/cases/{case_code}/file")
def case_file(case_code: str, db: Session = Depends(get_db)):
    """用例即文件：把库内 schema_json.code_file 以独立 .py 测试文件形态返回（单一事实来源是数据库）。"""
    from app.models import Cases

    c = db.query(Cases).filter(Cases.code == case_code).first()
    if c is None:
        raise ApiError(404, "用例不存在", 404)
    schema = _normalize_case_schema(c.schema_json) or {}
    code_text = str(schema.get("code_file") or "")
    if not code_text.strip():
        raise ApiError(404, "该用例没有可预览的测试源码", 404)
    tf = (c.target_function or "").split(".")[-1] if c.target_function else ""
    short = c.code.rsplit("-", 1)[-1]
    filename = f"test_{tf}__{short}.py" if tf else f"{c.code}.py"
    return ok({"filename": filename, "content": code_text, "size": len(code_text.encode("utf-8"))})


@app.post("/api/cases/{case_id}/delete")
def delete_case(case_id: int, request: Request, db: Session = Depends(get_db)):
    """删除用例（已入库的需 admin；删除即移除，历史 run/trace 保留）。"""
    user = getattr(request.state, "user", {"role": "viewer"})
    c = crud_cases.get(db, case_id)
    if c is None:
        raise ApiError(404, "case 不存在", 404)
    code = c.code
    crud_cases.remove(db, id=case_id)
    from app.core.trace import emit

    emit("生成", user.get("username", "-"), f"用例 {code} 删除")
    return ok({"deleted": code})


@app.put("/api/cases/{case_id}")
async def update_case(case_id: int, request: Request, data: CaseUpdateIn, db: Session = Depends(get_db)):
    """编辑用例元数据（title/status/review_note/confidence）。"""
    body = data.model_dump(exclude_unset=True)  # PATCH 语义：只更新调用方显式传入的字段
    fields = {"title", "status", "review_note", "confidence", "stale"}
    patch = {k: v for k, v in body.items() if k in fields}
    if not patch:
        raise ApiError(1001, "无可更新字段（支持 title/status/review_note/confidence/stale）")
    if "status" in patch and patch["status"] not in ("草稿", "待人审", "已入库", "已替换"):
        raise ApiError(1001, "非法状态")
    c = crud_cases.get(db, case_id)
    if c is None:
        raise ApiError(404, "case 不存在", 404)
    crud_cases.update(db, db_obj=c, obj_in=patch)
    code = c.code
    from app.core.trace import emit

    emit("生成", getattr(request.state, "user", {}).get("username", "-"), f"用例 {code} 更新: {', '.join(patch)}")
    return ok({"code": code, **patch})


@app.post("/api/cases/{case_id}/review")
async def review_case(case_id: int, data: CaseReviewIn, db: Session = Depends(get_db)):
    from app.core.trace import emit

    body = data.model_dump()
    action = body.get("action")  # approve | reject
    note = body.get("note", "")
    c = crud_cases.get(db, case_id)
    if c is None:
        raise ApiError(404, "case 不存在", 404)
    status = "已入库" if action == "approve" else "草稿"
    crud_cases.update(db, db_obj=c, obj_in={"status": status, "review_note": note})
    code, trace = c.code, c.trace_id
    emit("生成", "qa-reviewer", f"用例 {code} 人审{'通过' if action == 'approve' else '驳回'}: {note}", trace_id=trace)
    return ok({"code": code, "status": status})


# ---------------- 执行记录（M1） ----------------


@app.get("/api/runs")
def list_runs(limit: int = 100, db: Session = Depends(get_db)):
    from app.models import Runs

    rows = db.query(Runs).order_by(Runs.id.desc()).limit(limit).all()
    return ok(
        [
            {
                "code": r.code,
                "target": r.target,
                "layer": r.layer,
                "trigger": r.trigger,
                "sandbox_status": r.sandbox_status,
                "pass_total": r.pass_total,
                "pass_count": r.pass_count,
                "coverage": r.coverage,
                "repair_rounds": r.repair_rounds,
                "cost_s": r.cost_s,
                "status": r.status,
                "trace_id": r.trace_id,
                "req_code": r.req_code,
                "gen_id": r.gen_id,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    )


@app.post("/api/runs/{run_code}/rerun")
def rerun_run(run_code: str, db: Session = Depends(get_db)):
    from app.api.generations import new_generation
    from app.models import Generations, Runs

    run = db.query(Runs).filter(Runs.code == run_code).first()
    if run is None:
        raise ApiError(404, "run 不存在", 404)
    gen = db.query(Generations).filter(Generations.code == run.gen_id).first()
    if gen is None:
        raise ApiError(404, "原生成不存在，无法重跑", 404)
    return ok(new_generation({"function": gen.target_function, "repo_id": gen.repo_id or 0, "layer": gen.layer, "source_req": gen.req_code}))


# ---------------- 追溯（M1 最小版：查链路） ----------------


@app.get("/api/traces")
def list_traces(limit: int = 200, type: str = ""):
    """事件台账：最新在前，供「日志 / 追溯」页浏览全量留痕。"""
    from app.core.trace import query as trace_query

    events = trace_query(trace_type=type, limit=min(max(limit, 1), 500))
    return ok({"count": len(events), "events": list(reversed(events))})


@app.get("/api/traces/{trace_id}")
def get_trace(trace_id: str):
    from app.core.trace import query as trace_query

    events = trace_query(trace_id=trace_id)
    return ok(
        {
            "trace_id": trace_id,
            "count": len(events),
            "events": events,
        }
    )


# 各域路由模块（文件尾部导入注册，避免循环依赖）
from app.api import assistant as _assistant  # noqa: E402, F401
from app.api import auth as _auth  # noqa: E402, F401
from app.api import codegraph as _codegraph  # noqa: E402, F401  代码分析：影响面/聚类/调用环/执行流
from app.api import contracts as _contracts  # noqa: E402, F401
from app.api import graph as _graph  # noqa: E402, F401
from app.api import integrations as _integrations  # noqa: E402, F401  外部集成：URL 导入 / MCP 安装
from app.api import kg as _kg  # noqa: E402, F401  知识图谱：LightRAG 全量移植路由
from app.api import knowledge as _knowledge  # noqa: E402, F401  KG 简版 + 摄入健康 + LLM 缓存 + RAG 评估
from app.api import knowledge_assets as _knowledge_assets  # noqa: E402, F401  入口②：知识资产统一上传
from app.api import plans as _plans  # noqa: E402, F401
from app.api import repo_upload as _repo_upload  # noqa: E402, F401  入口①：上传代码包建仓
from app.api import requirements as _requirements  # noqa: E402, F401

app.include_router(_integrations.router)
app.include_router(_repo_upload.router)
app.include_router(_knowledge_assets.router)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=get_settings().app_port, log_config=None)
