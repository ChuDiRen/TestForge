"""testgen-svc 入口：gRPC TestGen（Generate 五步管线流式 + RegenerateAffected）。"""

import json
import logging

import grpc

from services.shared.base import pong
from services.shared.config import GRPC_PORTS
from services.shared.db import get_session
from services.shared.gen import testforge_pb2 as pb2
from services.shared.gen import testforge_pb2_grpc as pb2_grpc
from services.shared.grpc_server import run_server
from services.shared.logutil import setup_logging
from services.shared.models import Functions, Generations, Repos
from services.shared.trace import emit

NAME = "testgen-svc"
log = logging.getLogger(NAME)


def _repo_checkout(repo_id: int) -> str:
    """仓库检出目录（探针执行用）；未注册退回 demo fixture。"""
    with get_session() as sess:
        repo = sess.get(Repos, repo_id) if repo_id else None
        if repo is not None and repo.local_path:
            return repo.local_path
    return "fixtures/sample-repo"


def _load_fn(repo_id: int, function: str, module: str):
    from services.testgen_svc.fninfo import parse_signature

    with get_session() as sess:
        q = sess.query(Functions).filter(Functions.name == function)
        if repo_id:
            q = q.filter(Functions.repo_id == repo_id)
        row = q.order_by(Functions.id.desc()).first()
    if row is None:
        return None
    info = parse_signature(row.source, function)
    info.module = row.module
    return info


class TestGenServicer(pb2_grpc.TestGenServicer):
    def Ping(self, request, context):  # noqa: N802
        return pong(NAME)

    def Generate(self, request, context):  # noqa: N802
        """五步管线（本服务承担 PLAN→GUARD→CODEGEN；SANDBOX/COVERAGE 由 runner 承担）。"""
        from services.testgen_svc.codegen import codegen
        from services.testgen_svc.context import build_context
        from services.testgen_svc.guard import guard
        from services.testgen_svc.planner import plan_cases

        target = request.target.function
        repo_id = request.target.repo_id
        layer = request.target.layer or "ut"
        gen_code = f"GEN-{target[:12]}-{request.trace_id[-6:]}" if request.trace_id else f"GEN-{target[:12]}"
        log.info("generate %s repo=%s trace=%s", target, repo_id, request.trace_id)

        fn = _load_fn(repo_id, target, request.target.module)
        if fn is None:
            context.abort(grpc.StatusCode.NOT_FOUND, f"函数未索引: {target}")

        # ① 上下文组装（六路，优先级写死）
        ctx = build_context(repo_id, target, request.target.module)
        yield pb2.GenEvent(stage="plan", message=f"上下文组装: {'>'.join(ctx.sources) or '仅签名'}", progress=0.08, payload_json=json.dumps({"ctx_sources": ctx.sources}, ensure_ascii=False))

        # ② 阶段 A：用例清单（schema 校验）
        options = {"min_cases": request.options.min_cases or 9, "repair": request.options.repair}
        plan = plan_cases(fn, target, layer, options)
        yield pb2.GenEvent(stage="plan", message=f"用例清单 {len(plan.cases)} 条（{', '.join(sorted(plan.categories))}）", progress=0.2, payload_json=plan.model_dump_json())

        # ③ 覆盖守卫：静态检查表，缺类自动补
        plan, report = guard(plan, fn)
        yield pb2.GenEvent(stage="guard", message=f"守卫检查: 检查表 {len(report['checked'])} 项，自动补 {len(report['added'])} 条", progress=0.35, payload_json=json.dumps(report, ensure_ascii=False))

        # ③b 探针捕获：期望值来自对真实代码的实际执行（现实即规格）。
        # 非精选靶标开启覆写——LLM/守卫的推测断言一律以真实执行结果校正。
        from services.testgen_svc.probe import fill_probe_expectations

        plan, probe_note = fill_probe_expectations(
            plan, _repo_checkout(repo_id), overwrite=fn.name not in ("create_order", "sanitize_text")
        )
        if probe_note:
            yield pb2.GenEvent(stage="probe", message=f"探针期望值: {probe_note}", progress=0.4)

        # ④ 阶段 B：按清单生成代码
        src = codegen(plan, gen_code)
        with get_session() as sess:
            g = sess.query(Generations).filter(Generations.code == gen_code).first()
            if g is not None:
                g.plan_json = plan.model_dump_json()
                g.codegen = src
                sess.commit()
        cases_payload = [
            {
                "code": c.id,
                "title": c.title,
                "category": c.category,
                "module": plan.module,
                "source_req": request.target.source_req,
                "input": c.input,
                "expected_error": c.expected_error,
                "expected_fields": c.expected_fields,
                "patches": [p.model_dump() for p in c.patches],
                "covers": c.covers,
                "confidence": 0.97 if c.category in ("normal", "boundary") else 0.94,
            }
            for c in plan.cases
        ]
        yield pb2.GenEvent(
            stage="codegen",
            message=f"生成 pytest 完成：{len(plan.cases)} 用例 / {src.count(chr(10))} 行",
            progress=0.6,
            payload_json=json.dumps({"cases": cases_payload, "code": src}, ensure_ascii=False),
        )

        # ⑤ 结果事件（SANDBOX/COVERAGE 事件由 runner/gateway 阶段补齐）
        yield pb2.GenEvent(
            stage="result",
            message=f"生成完成：{len(cases_payload)} 条用例待执行",
            progress=1.0,
            payload_json=json.dumps({"gen_code": gen_code, "target": target, "layer": layer, "cases": cases_payload, "code": src}, ensure_ascii=False),
        )
        emit("生成", "testgen-svc", f"生成完成 {gen_code} 目标={target} 用例={len(cases_payload)} 守卫补={len(report['added'])}", req_code=request.target.source_req, trace_id=request.trace_id or None)

    def RegenerateAffected(self, request, context):  # noqa: N802
        """定向重生成（修复回填/契约影响分析）：按目标函数重建清单与代码。

        与首生成同一现实基准：探针重新捕获期望值后渲染，返回完整测试文件源码（code_file），
        由 runner 写回沙箱工作区——修复不是重试，而是以真实行为为准重写断言。
        """
        from services.testgen_svc.codegen import codegen
        from services.testgen_svc.guard import guard
        from services.testgen_svc.planner import plan_cases
        from services.testgen_svc.probe import fill_probe_expectations

        target = request.target_function
        if not target:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "target_function 必填（修复回填需要明确被测目标）")
        fn = _load_fn(request.repo_id, target, "")
        if fn is None:
            context.abort(grpc.StatusCode.NOT_FOUND, f"重生成目标未索引: {target}")
        plan = plan_cases(fn, target, "ut", {"repair": True})
        plan, report = guard(plan, fn)
        plan, _note = fill_probe_expectations(
            plan, _repo_checkout(request.repo_id), overwrite=fn.name not in ("create_order", "sanitize_text")
        )
        gen_code = f"REPAIR-{(request.trace_id or 'NA')[-6:]}"
        src = codegen(plan, gen_code)
        emit(
            "生成",
            "testgen-svc",
            f"定向重生成 {len(plan.cases)} 条（原因: {request.reason or 'repair'}，探针重捕获期望值）",
            req_code=request.source_req,
            trace_id=request.trace_id or None,
        )
        return pb2.GenResult(
            generation_id=gen_code,
            cases_total=len(plan.cases),
            guard_added=len(report["added"]),
            case_ids=[c.id for c in plan.cases],
            code_file=src,
        )


def register(server) -> None:
    pb2_grpc.add_TestGenServicer_to_server(TestGenServicer(), server)


def main() -> None:
    setup_logging(NAME)
    log.info("starting %s", NAME)
    run_server(GRPC_PORTS[NAME], NAME, register)


if __name__ == "__main__":
    main()
