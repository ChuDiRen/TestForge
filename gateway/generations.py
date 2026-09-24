"""生成编排：POST /api/generations 后台线程跑完整闭环：

TestGen.Generate 流式事件 → generation_events 落库（SSE 回放）
→ TestRunner.Execute（沙箱+修复循环）→ 用例/执行记录入库 → trace 双事件。
"""

import json
import logging
import threading
import uuid

from services.shared.config import GRPC_PORTS, get_settings
from services.shared.db import get_session
from services.shared.grpc_client import grpc_call, grpc_stream
from services.shared.logging import get_trace_id, set_trace_id
from services.shared.models import Cases, GenerationEvents, Generations, Runs
from services.shared.trace import emit

log = logging.getLogger("gateway.gen")


def new_generation(payload: dict) -> dict:
    target = payload.get("function") or payload.get("target") or ""
    if not target:
        from gateway.envelope import ApiError

        raise ApiError(1001, "function 必填")
    repo_id = int(payload.get("repo_id") or 0)
    layer = payload.get("layer") or "ut"
    source_req = payload.get("source_req") or ""
    trace_id = get_trace_id()
    gen_code = f"GEN-{uuid.uuid4().hex[:8].upper()}"
    with get_session() as sess:
        sess.add(
            Generations(
                code=gen_code,
                repo_id=repo_id or None,
                target_function=target,
                layer=layer,
                status="running",
                trace_id=trace_id,
                req_code=source_req,
            )
        )
        sess.commit()
    t = threading.Thread(target=_run_pipeline, args=(gen_code, repo_id, target, layer, source_req, trace_id), daemon=True)
    t.start()
    return {"generation_id": gen_code, "trace_id": trace_id, "status": "running"}


def _event(gen_code: str, kind: str, stage: str, message: str, payload: str = "", progress: float = 0.0) -> None:
    with get_session() as sess:
        sess.add(GenerationEvents(gen_code=gen_code, kind=kind, stage=stage, message=message, payload_json=payload, progress=progress))
        sess.commit()


def _run_pipeline(gen_code: str, repo_id: int, target: str, layer: str, source_req: str, trace_id: str) -> None:
    set_trace_id(trace_id)
    try:
        # ① 生成管线（plan/guard/codegen 事件流式落库）
        final: dict = {}
        for ev in grpc_stream(
            "testgen-svc",
            GRPC_PORTS["testgen-svc"],
            "TestGen",
            "Generate",
            {
                "trace_id": trace_id,
                "target": {"function": target, "layer": layer, "repo_id": repo_id, "source_req": source_req},
                "options": {"min_cases": 9, "repair": True},
            },
            timeout=120,
        ):
            _event(gen_code, "stage" if ev.get("stage") != "result" else "result", ev.get("stage", ""), ev.get("message", ""), ev.get("payload_json", ""), float(ev.get("progress", 0)))
            if ev.get("stage") == "result":
                final = json.loads(ev.get("payload_json") or "{}")
        if not final:
            raise RuntimeError("生成未返回 result 事件")

        cases = final.get("cases", [])
        code_file = final.get("code", "")

        # ② 沙箱执行（runner 内部含修复循环）
        _event(gen_code, "stage", "sandbox", f"沙箱执行 {len(cases)} 用例（SANDBOX_MODE={get_settings().sandbox_mode}）", progress=0.7)
        run_code = f"RUN-{uuid.uuid4().hex[:8].upper()}"
        suite_cases = [
            {
                "code": c["code"],
                "title": c["title"],
                "layer": layer,
                "module": c.get("module", ""),
                "category": c["category"],
                "schema_json": json.dumps({"code_file": code_file, **c}, ensure_ascii=False),
                "source_req": source_req,
            }
            for c in cases
        ]
        import time

        t0 = time.time()
        report = grpc_call(
            "runner-svc",
            GRPC_PORTS["runner-svc"],
            "TestRunner",
            "Execute",
            {"run_id": run_code, "cases": suite_cases, "repo_id": repo_id, "trace_id": trace_id},
            timeout=600,
        )
        report["cost_s"] = report.get("cost_s") or int(time.time() - t0)
        _event(gen_code, "stage", "sandbox", f"执行完成 {report['pass_count']}/{report['pass_total']} 通过（{report['sandbox_status']}）", json.dumps(report, ensure_ascii=False), progress=0.85)
        _event(gen_code, "stage", "coverage", f"分支覆盖率 {report['coverage']}% · 修复 {report['repair_rounds']} 轮", progress=0.95)

        # ③ 入库：runs + cases（通过→已入库，失败→草稿）
        case_results = {}
        try:
            log_data = json.loads(report.get("log_json") or "{}")
            case_results = log_data.get("cases", {})
        except json.JSONDecodeError:
            pass
        with get_session() as sess:
            sess.add(
                Runs(
                    code=report.get("run_id") or run_code,
                    target=target,
                    layer=layer,
                    trigger="手动",
                    sandbox_status=report["sandbox_status"],
                    pass_total=report["pass_total"],
                    pass_count=report["pass_count"],
                    coverage=report["coverage"],
                    repair_rounds=report["repair_rounds"],
                    cost_s=report["cost_s"],
                    status="success" if report["status"] == "success" else "failed",
                    trace_id=trace_id,
                    req_code=source_req,
                    gen_id=gen_code,
                    log_json=report.get("log_json", ""),
                )
            )
            for c in cases:
                ok = case_results.get(c["code"]) == "passed"
                code = f"CASE-{gen_code[-6:]}-{c['code']}"
                sess.add(
                    Cases(
                        code=code,
                        layer=layer,
                        title=c["title"],
                        module=c.get("module", ""),
                        category=c["category"],
                        schema_json=json.dumps(c, ensure_ascii=False),
                        source_req=source_req,
                        confidence=c.get("confidence", 0.9),
                        status="已入库" if ok else "草稿",
                        trace_id=trace_id,
                        gen_id=gen_code,
                        last_run_ok=ok,
                        target_function=target,
                        repo_id=repo_id or None,
                    )
                )
            sess.flush()
            # RAG 向量索引（pgvector / python 余弦）
            try:
                from services.shared.rag import index_case

                for c in cases:
                    index_case(
                        f"CASE-{gen_code[-6:]}-{c['code']}",
                        f"{c['title']} {c['category']} {target} {json.dumps(c.get('input', {}), ensure_ascii=False)[:300]}",
                    )
            except Exception as exc:  # noqa: BLE001
                log.warning("rag index failed: %s", exc)
            g = sess.query(Generations).filter(Generations.code == gen_code).first()
            if g is not None:
                g.status = "done"
            sess.commit()

        result_payload = json.dumps(
            {
                "gen_code": gen_code,
                "run_id": report.get("run_id") or run_code,
                "cases_total": len(cases),
                "passed": report["pass_count"],
                "total": report["pass_total"],
                "coverage": report["coverage"],
                "repair_rounds": report["repair_rounds"],
                "trace_id": trace_id,
            },
            ensure_ascii=False,
        )
        _event(gen_code, "result", "result", f"闭环完成：{report['pass_count']}/{report['pass_total']} 通过 · 覆盖率 {report['coverage']}%", result_payload, 1.0)
    except Exception as exc:  # noqa: BLE001
        log.exception("pipeline %s failed", gen_code)
        with get_session() as sess:
            g = sess.query(Generations).filter(Generations.code == gen_code).first()
            if g is not None:
                g.status = "failed"
            sess.commit()
        _event(gen_code, "result", "result", f"管线失败: {exc}", "", 1.0)
        emit("生成", "gateway", f"生成管线失败 {gen_code}: {exc}", trace_id=trace_id)
    finally:
        set_trace_id("-")


def stream_events(gen_code: str):
    """SSE：回放 generation_events，直到 result。"""
    import time

    cursor = 0
    start = time.time()
    while time.time() - start < 600:
        with get_session() as sess:
            rows = (
                sess.query(GenerationEvents)
                .filter(GenerationEvents.gen_code == gen_code, GenerationEvents.id > cursor)
                .order_by(GenerationEvents.id.asc())
                .all()
            )
            data = [
                {
                    "id": r.id,
                    "kind": r.kind,
                    "stage": r.stage,
                    "message": r.message,
                    "payload_json": r.payload_json,
                    "progress": r.progress,
                }
                for r in rows
            ]
        if not data:
            yield "event: ping\ndata: {}\n\n"
            time.sleep(0.5)
            continue
        for ev in data:
            cursor = max(cursor, ev["id"])
            yield f"event: {ev['kind']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
            if ev["kind"] == "result":
                return
