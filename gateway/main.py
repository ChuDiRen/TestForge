"""gateway 主应用：REST/SSE 唯一入口，服务间走 gRPC。"""

import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Integer, func

from gateway.envelope import ApiError, err, ok
from services.shared.config import GRPC_PORTS, VERSION, get_settings
from services.shared.db import get_session, init_db
from services.shared.grpc_client import grpc_call
from services.shared.logging import new_trace_id, set_trace_id, setup_logging
from services.shared.models import (
    Cases,
    Contracts,
    Defects,
    Repos,
    Requirements,
    Runs,
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
    init_db()
    if get_settings().mono:
        from services.shared.mono import register_all

        svc_list = register_all()
        log.info("backend v%s (MONO) up on :%d — in-process services: %s", VERSION, get_settings().gateway_port, ", ".join(svc_list))
    else:
        log.info("gateway v%s up on :%d (microservice mode)", VERSION, get_settings().gateway_port)
    yield


app = FastAPI(title="TestForge Gateway", version=VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Trace-Id"],
)


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
    with get_session() as sess:
        return ok(
            {
                "repos": sess.query(Repos).count(),
                "cases_total": sess.query(Cases).count(),
                "cases_by_layer": _count_by(sess, Cases.layer),
                "cases_by_status": _count_by(sess, Cases.status),
                "pending_reviews": sess.query(Cases).filter(Cases.status == "待人审").count(),
                "runs_total": sess.query(Runs).count(),
                "runs_pass_rate": _pass_rate(sess),
                "requirements_total": sess.query(Requirements).count(),
                "defects_open": sess.query(Defects).filter(Defects.status.notin_(["已关闭"])).count(),
                "wiki_pages": sess.query(WikiPages).count(),
                "wiki_stale": sess.query(WikiPages).filter(WikiPages.stale.is_(True)).count(),
                "contracts": sess.query(Contracts).count(),
            }
        )


def _count_by(sess, column) -> dict:  # type: ignore[no-untyped-def]
    rows = sess.query(column, func.count()).group_by(column).all()
    return {k or "": v for k, v in rows}


def _pass_rate(sess) -> float:  # type: ignore[no-untyped-def]
    total = sess.query(Runs).count()
    if not total:
        return 0.0
    passed = sess.query(Runs).filter(Runs.status == "success").count()
    return round(passed / total * 100, 1)


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


    from services.shared.models import WikiPages

    with get_session() as sess:
        rows = (
            sess.query(WikiPages.repo_id, func.count(), func.sum(func.cast(WikiPages.stale, Integer)))
            .group_by(WikiPages.repo_id)
            .all()
        )
        return ok(
            [
                {"repo_id": rid, "pages": total, "stale": int(stale or 0)}
                for rid, total, stale in rows
            ]
        )


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
    body = await request.json()
    url = (body.get("url") or "").strip()
    if not url:
        raise ApiError(1001, "url 必填")
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
    res = grpc_call(
        "repo-svc",
        GRPC_PORTS["repo-svc"],
        "RepoSvc",
        "Pull",
        {"repo_id": repo_id},
    )
    return ok(res)


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
def list_cases(layer: str = "", module: str = "", status: str = "", category: str = "", source_req: str = ""):
    from services.shared.models import Cases

    with get_session() as sess:
        q = sess.query(Cases)
        if layer:
            q = q.filter(Cases.layer == layer)
        if module:
            q = q.filter(Cases.module.contains(module))
        if status:
            q = q.filter(Cases.status == status)
        if category:
            q = q.filter(Cases.category == category)
        if source_req:
            q = q.filter(Cases.source_req == source_req)
        rows = q.order_by(Cases.id.desc()).limit(1000).all()
        by_layer = {}
        for lay in ("ut", "api", "fn", "e2e", "contract"):
            by_layer[lay] = sess.query(Cases).filter(Cases.layer == lay).count()
        return ok(
            {
                "total": len(rows),
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
                        "schema": json.loads(c.schema_json) if c.schema_json else {},
                    }
                    for c in rows
                ],
            }
        )


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


# M3 需求路由 + M4 契约路由（文件尾部导入注册，避免循环依赖）
from gateway import contracts as _contracts  # noqa: E402, F401
from gateway import plans as _plans  # noqa: E402, F401
from gateway import requirements as _requirements  # noqa: E402, F401

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=get_settings().gateway_port, log_config=None)
