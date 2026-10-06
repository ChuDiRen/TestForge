"""gateway 主应用：REST/SSE 唯一入口，服务间走 gRPC。

认证：除 /api/health 与 /api/auth/login 外全部需要 Bearer token（admin 全权，viewer 只读）。
"""

import hmac
import json
import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Integer, func, select

from gateway.envelope import ApiError, err, ok
from services.shared.auth import parse_token, renewed_token
from services.shared.config import GRPC_PORTS, VERSION, get_settings
from services.shared.db import get_session, init_db
from services.shared.grpc_client import grpc_call
from services.shared.logutil import new_trace_id, set_trace_id, setup_logging
from services.shared.models import (
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

log = logging.getLogger("gateway")

SERVICE_LIST = [
    ("repo-svc", "RepoSvc"),
    ("wiki-builder", "WikiBuilder"),
    ("contract-registry", "ContractRegistry"),
    ("req-svc", "ReqIngest"),
    ("testgen-svc", "TestGen"),
    ("runner-svc", "TestRunner"),
    ("trace-svc", "TraceLog"),
]


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    setup_logging("gateway", get_settings().log_level)
    s = get_settings()
    if s.env == "prod":
        problems = s.validate_for_prod()
        if problems:
            for p in problems:
                log.error("生产安全检查未通过: %s", p)
            raise RuntimeError(f"ENV=prod 安全检查未通过（{len(problems)} 项），拒绝启动——详见上方日志")
        log.info("生产安全检查通过（env=prod）")
    init_db()
    if get_settings().mono:
        from services.shared.mono import register_all

        svc_list = register_all()
        log.info("backend v%s (MONO) up on :%d — in-process services: %s", VERSION, get_settings().gateway_port, ", ".join(svc_list))
    else:
        log.info("gateway v%s up on :%d (microservice mode)", VERSION, get_settings().gateway_port)
    from gateway.jobs import recover_and_start

    workers = recover_and_start()
    log.info("job queue started with %d worker(s)", workers)
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
_USER_DISABLED_CACHE = _USER_STATUS_CACHE  # 兼容别名（auth_routes 清缓存用）
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
    """M0 验收点：网关→服务链路状态。单体外=进程内直调 Ping；微服务模式下走网络 Ping。"""
    items = []
    from services.shared.mono import _REGISTRY

    in_process = get_settings().mono and all(svc in _REGISTRY for _, svc in SERVICE_LIST)
    for name, svc in SERVICE_LIST:
        if in_process:
            try:
                res = grpc_call(name, GRPC_PORTS[name], svc, "Ping", {"client": "gateway"})
                items.append({"name": name, "port": GRPC_PORTS[name], "ok": True, "db_ok": res.get("db_ok", False), "version": res.get("version", "")})
            except Exception as exc:  # noqa: BLE001
                items.append({"name": name, "port": GRPC_PORTS[name], "ok": False, "db_ok": False, "error": str(exc)[:120]})
            continue
        try:
            res = grpc_call(name, GRPC_PORTS[name], svc, "Ping", {"client": "gateway"}, timeout=3.0)
            items.append({"name": name, "port": GRPC_PORTS[name], "ok": True, "db_ok": res.get("db_ok", False), "version": res.get("version", "")})
        except Exception as exc:  # noqa: BLE001
            items.append({"name": name, "port": GRPC_PORTS[name], "ok": False, "db_ok": False, "error": str(exc)[:120]})
    return ok({"services": items, "all_green": all(i["ok"] and i["db_ok"] for i in items), "mode": "mono" if in_process else "micro"})


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
def list_wiki(repo_id: int = 0):
    from services.shared.models import WikiPages

    with get_session() as sess:
        q = sess.query(WikiPages)
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
def wiki_health():
    from services.shared.models import WikiPages

    with get_session() as sess:
        rows = sess.query(WikiPages.repo_id, func.count(), func.sum(func.cast(WikiPages.stale, Integer))).group_by(WikiPages.repo_id).all()
        return ok([{"repo_id": rid, "pages": total, "stale": int(stale or 0)} for rid, total, stale in rows])


@app.get("/api/wiki/{page_id}")
def get_wiki_page(page_id: int):
    from services.shared.models import WikiPages

    with get_session() as sess:
        w = sess.get(WikiPages, page_id)
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


@app.post("/api/wiki/rebuild")
async def rebuild_wiki(request: Request):
    """一键重建：body {repo_id, full?}。full=true 全量重编译并清 stale。"""

    body = await request.json()
    repo_id = int(body.get("repo_id") or 0)
    full = bool(body.get("full"))
    with get_session() as sess:
        repo = sess.get(Repos, repo_id)
        if repo is None:
            raise ApiError(404, "repo 不存在", 404)
    res = grpc_call(
        "wiki-builder",
        GRPC_PORTS["wiki-builder"],
        "WikiBuilder",
        "Rebuild",
        {"repo_id": repo_id, "from_rev": "" if full else repo.head_rev, "to_rev": repo.head_rev, "changed_files": [] if full else ["__stale__"]},
        timeout=120,
    )
    return ok(res)


# ---------------- 仓库（M1 全量；M0 落库+列表） ----------------


@app.get("/api/repos")
def list_repos():
    with get_session() as sess:
        rows = sess.query(Repos).order_by(Repos.id.desc()).all()
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


@app.post("/api/repos")
async def create_repo(request: Request):
    from services.repo_svc.gitops import GitError, validate_remote_url
    from services.shared.config import get_settings as _gs

    body = await request.json()
    url = (body.get("url") or "").strip()
    if not url:
        raise ApiError(1001, "url 必填")
    try:
        validate_remote_url(url, allow_local=_gs().allow_local_repo_url)
    except GitError as exc:
        raise ApiError(1003, str(exc), 400) from exc
    res = grpc_call(
        "repo-svc",
        GRPC_PORTS["repo-svc"],
        "RepoSvc",
        "Register",
        {
            "url": url,
            "branch": body.get("branch") or "main",
            "credential_ref": body.get("credential_ref") or "",
            "webhook": bool(body.get("webhook")),
        },
    )
    return ok(res)


@app.post("/api/repos/{repo_id}/pull")
def pull_repo(repo_id: int):
    """拉取 + 增量索引 + 变更驱动回归（源码变更的函数 → 关联用例标 stale → 自动回归）。"""
    from gateway.regression import regress_changed

    res = grpc_call(
        "repo-svc",
        GRPC_PORTS["repo-svc"],
        "RepoSvc",
        "Pull",
        {"repo_id": repo_id},
        timeout=300,
    )
    summary = regress_changed(repo_id, res.get("changed_functions") or [])
    return ok({**res, "regression": summary})


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
        from gateway.regression import regress_changed
        from services.shared.grpc_client import grpc_call as _call
        from services.shared.trace import emit

        tid = new_trace_id()
        set_trace_id(tid)
        try:
            res = _call("repo-svc", GRPC_PORTS["repo-svc"], "RepoSvc", "Pull", {"repo_id": repo_id}, timeout=300)
            summary = regress_changed(repo_id, res.get("changed_functions") or [])
            emit(
                "仓库",
                "webhook",
                f"webhook 拉取完成 repo={repo_id} rev={str(res.get('head_rev') or '')[:8]} 变更函数={len(res.get('changed_functions') or [])} 回归={summary}",
                trace_id=tid,
            )
        except Exception as exc:  # noqa: BLE001
            emit("仓库", "webhook", f"webhook 拉取失败 repo={repo_id}: {exc}", trace_id=tid)
        finally:
            set_trace_id("-")

    threading.Thread(target=_pull_and_regress, daemon=True).start()
    return ok({"accepted": True, "repo_id": repo_id})


# ---------------- 函数（M1 生成目标） ----------------


@app.get("/api/functions")
def list_functions(repo_id: int = 0, module: str = "", name: str = ""):
    from services.shared.models import Functions

    with get_session() as sess:
        q = sess.query(Functions)
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
async def create_generation(request: Request):
    from gateway.generations import new_generation

    body = await request.json()
    return ok(new_generation(body))


@app.post("/api/generations/batch")
async def create_generation_batch(request: Request):
    """批量生成：body {repo_id, module?|names?[], layer?}。按模块圈选或显式函数名，逐个入队。"""
    from gateway.generations import new_batch

    body = await request.json()
    return ok(new_batch(body))


@app.get("/api/jobs")
def list_jobs(status: str = "", limit: int = 50):
    """任务队列台账（生成/回归），新→旧。"""
    with get_session() as sess:
        q = sess.query(Jobs)
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
def get_job(job_code: str):
    with get_session() as sess:
        j = sess.query(Jobs).filter(Jobs.code == job_code).first()
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
def export_generation(gen_code: str):
    """导出生成的测试文件（前端触发下载）。"""
    with get_session() as sess:
        g = sess.query(Generations).filter(Generations.code == gen_code).first()
        if g is None or not g.codegen:
            raise ApiError(404, "生成不存在或无代码产物", 404)
        src = g.codegen
    filename = f"test_tf_gen_{gen_code.lower()}.py"
    return ok({"filename": filename, "content": src, "lines": src.count("\n") + 1})


@app.post("/api/generations/{gen_code}/export-to-repo")
def export_generation_to_repo(gen_code: str):
    """把生成的测试文件真实写入仓库检出的 tests/testforge_generated/ 目录。"""
    with get_session() as sess:
        g = sess.query(Generations).filter(Generations.code == gen_code).first()
        if g is None or not g.codegen:
            raise ApiError(404, "生成不存在或无代码产物", 404)
        src = g.codegen
        repo = sess.get(Repos, g.repo_id) if g.repo_id else None
    if repo is None or not repo.local_path:
        raise ApiError(404, "生成未绑定可用仓库检出", 404)
    dest_dir = Path(repo.local_path) / "tests" / "testforge_generated"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"test_tf_gen_{gen_code.lower()}.py"
    dest.write_text(src, encoding="utf-8")
    return ok({"path": str(dest), "filename": dest.name, "lines": src.count("\n") + 1})


@app.get("/api/generations/{gen_code}")
def get_generation(gen_code: str):
    from services.shared.models import GenerationEvents, Generations

    with get_session() as sess:
        g = sess.query(Generations).filter(Generations.code == gen_code).first()
        if g is None:
            raise ApiError(404, "generation 不存在", 404)
        events = sess.query(GenerationEvents).filter(GenerationEvents.gen_code == gen_code).order_by(GenerationEvents.id).all()
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

    from gateway.generations import stream_events

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
):
    """用例库（服务端分页：page/page_size，total 为全量计数）。stale=true 只看待回归。"""
    from services.shared.models import Cases

    with get_session() as sess:
        q = sess.query(Cases)
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
            {k: v for k, v in sess.query(Cases.layer, func.count()).filter(Cases.status != "已替换").group_by(Cases.layer).all()}
        )
        return ok(
            {
                "total": total,
                "page": page,
                "page_size": page_size,
                "stale_total": sess.query(Cases).filter(Cases.stale.is_(True)).count(),
                "by_layer": by_layer,
                "by_category": _count_by(sess, Cases.category),
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
def case_file(case_code: str):
    """用例即文件：把库内 schema_json.code_file 以独立 .py 测试文件形态返回（单一事实来源是数据库）。"""
    from services.shared.models import Cases

    with get_session() as sess:
        c = sess.query(Cases).filter(Cases.code == case_code).first()
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


@app.delete("/api/cases/{case_id}")
def delete_case(case_id: int, request: Request):
    """删除用例（已入库的需 admin；删除即移除，历史 run/trace 保留）。"""
    user = getattr(request.state, "user", {"role": "viewer"})
    with get_session() as sess:
        c = sess.get(Cases, case_id)
        if c is None:
            raise ApiError(404, "case 不存在", 404)
        code = c.code
        sess.delete(c)
        sess.commit()
    from services.shared.trace import emit

    emit("生成", user.get("username", "-"), f"用例 {code} 删除")
    return ok({"deleted": code})


@app.put("/api/cases/{case_id}")
async def update_case(case_id: int, request: Request):
    """编辑用例元数据（title/status/review_note/confidence）。"""
    body = await request.json()
    fields = {"title", "status", "review_note", "confidence", "stale"}
    patch = {k: v for k, v in body.items() if k in fields}
    if not patch:
        raise ApiError(1001, "无可更新字段（支持 title/status/review_note/confidence/stale）")
    if "status" in patch and patch["status"] not in ("草稿", "待人审", "已入库", "已替换"):
        raise ApiError(1001, "非法状态")
    with get_session() as sess:
        c = sess.get(Cases, case_id)
        if c is None:
            raise ApiError(404, "case 不存在", 404)
        for k, v in patch.items():
            setattr(c, k, v)
        code = c.code
        sess.commit()
    from services.shared.trace import emit

    emit("生成", getattr(request.state, "user", {}).get("username", "-"), f"用例 {code} 更新: {', '.join(patch)}")
    return ok({"code": code, **patch})


@app.post("/api/cases/{case_id}/review")
async def review_case(case_id: int, request: Request):
    from services.shared.models import Cases
    from services.shared.trace import emit

    body = await request.json()
    action = body.get("action")  # approve | reject
    note = body.get("note", "")
    with get_session() as sess:
        c = sess.get(Cases, case_id)
        if c is None:
            raise ApiError(404, "case 不存在", 404)
        c.status = "已入库" if action == "approve" else "草稿"
        c.review_note = note
        sess.commit()
        code, trace = c.code, c.trace_id
    emit("生成", "qa-reviewer", f"用例 {code} 人审{'通过' if action == 'approve' else '驳回'}: {note}", trace_id=trace)
    return ok({"code": code, "status": "已入库" if action == "approve" else "草稿"})


# ---------------- 执行记录（M1） ----------------


@app.get("/api/runs")
def list_runs(limit: int = 100):
    from services.shared.models import Runs

    with get_session() as sess:
        rows = sess.query(Runs).order_by(Runs.id.desc()).limit(limit).all()
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
def rerun_run(run_code: str):
    from gateway.generations import new_generation
    from services.shared.models import Generations, Runs

    with get_session() as sess:
        run = sess.query(Runs).filter(Runs.code == run_code).first()
        if run is None:
            raise ApiError(404, "run 不存在", 404)
        gen = sess.query(Generations).filter(Generations.code == run.gen_id).first()
    if gen is None:
        raise ApiError(404, "原生成不存在，无法重跑", 404)
    return ok(new_generation({"function": gen.target_function, "repo_id": gen.repo_id or 0, "layer": gen.layer, "source_req": gen.req_code}))


# ---------------- 追溯（M1 最小版：查链路） ----------------


@app.get("/api/traces")
def list_traces(limit: int = 200, type: str = ""):
    """事件台账：最新在前，供「日志 / 追溯」页浏览全量留痕。"""
    from services.shared.trace import query as trace_query

    events = trace_query(trace_type=type, limit=min(max(limit, 1), 500))
    return ok({"count": len(events), "events": list(reversed(events))})


@app.get("/api/traces/{trace_id}")
def get_trace(trace_id: str):
    from services.shared.trace import query as trace_query

    events = trace_query(trace_id=trace_id)
    return ok(
        {
            "trace_id": trace_id,
            "count": len(events),
            "events": events,
        }
    )


# M3 需求路由 + M4 契约路由 + 知识增强路由（文件尾部导入注册，避免循环依赖）
from gateway import assistant as _assistant_routes  # noqa: E402, F401
from gateway import auth_routes as _auth_routes  # noqa: E402, F401
from gateway import contracts as _contracts  # noqa: E402, F401
from gateway import graph as _graph  # noqa: E402, F401
from gateway import knowledge as _knowledge  # noqa: E402, F401
from gateway import plans as _plans  # noqa: E402, F401
from gateway import requirements as _requirements  # noqa: E402, F401

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=get_settings().gateway_port, log_config=None)
