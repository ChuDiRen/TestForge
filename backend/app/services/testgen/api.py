"""testgen-svc 平铺 API：五步生成管线（生成器逐事件产出）+ 定向重生成（原 gRPC TestGen 契约）。"""

import json
import logging

from app.core import health
from app.db.session import get_session
from app.models import Functions, Generations, Repos

log = logging.getLogger("testgen-svc.api")


def ping() -> dict:
    return health.ping("testgen-svc")


def _confidence(cases: list) -> list[float]:
    """置信度按证据分级（确定性规则，非拍脑袋数字）：

    - 0.98：期望值有真实依据（探针对真实代码执行捕获 / 精选清单人工核实）；
    - 0.90：仅输入设计（守卫补齐的宽松断言 / LLM 设计输入、断言待执行校正）。
    """
    out = []
    for c in cases:
        evidenced = bool(c.expected_error or c.expected_error_type or c.expected_fields or c.assert_return)
        out.append(0.98 if evidenced else 0.9)
    return out


def _repo_checkout(repo_id: int) -> str:
    """仓库检出目录（探针执行用）；未注册退回 demo fixture。"""
    with get_session() as sess:
        repo = sess.get(Repos, repo_id) if repo_id else None
        if repo is not None and repo.local_path:
            return repo.local_path
    return "fixtures/sample-repo"


def _load_fn(repo_id: int, function: str, module: str):
    from app.services.testgen.fninfo import parse_signature

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


def generate(
    trace_id: str,
    function: str,
    layer: str = "ut",
    repo_id: int = 0,
    module: str = "",
    source_req: str = "",
    min_cases: int = 9,
    repair: bool = True,
):
    """五步管线（本服务承担 PLAN→GUARD→PROBE→CODEGEN；SANDBOX/COVERAGE 由 runner 承担）。

    逐个 yield GenEvent dict：{stage, message, payload_json, progress}；
    stage ∈ plan|guard|probe|codegen|result。
    """
    from app.services.testgen.codegen import codegen
    from app.services.testgen.context import build_context
    from app.services.testgen.guard import guard
    from app.services.testgen.planner import plan_cases

    target = function
    gen_code = f"GEN-{target[:12]}-{trace_id[-6:]}" if trace_id else f"GEN-{target[:12]}"
    log.info("generate %s repo=%s layer=%s trace=%s", target, repo_id, layer, trace_id)

    # Web 层（接口/E2E）：从实时契约与旅程推导，对真实运行的服务执行
    if layer in ("api", "e2e"):
        from app.services.testgen.webcodegen import render_web_file
        from app.services.testgen.webplan import plan_web_cases

        plan = plan_web_cases(layer)
        yield {
            "stage": "plan",
            "message": f"Web 层清单 {len(plan.cases)} 条（base={plan.cases[0].input.get('base_url') if plan.cases else ''}）",
            "progress": 0.2,
            "payload_json": plan.model_dump_json(),
        }
        src = render_web_file(plan, gen_code)
        with get_session() as sess:
            g = sess.query(Generations).filter(Generations.code == gen_code).first()
            if g is not None:
                g.plan_json = plan.model_dump_json()
                g.codegen = src
                sess.commit()
        # web 层引用：依据来自实时契约抓取（openapi 端点即证据源）
        web_citations = [
            {"kind": "contract", "ref": f"openapi@{plan.cases[0].input.get('base_url', '')}/openapi.json", "detail": f"实时抓取 {len(plan.cases)} 用例路径/方法"}
            if plan.cases
            else {"kind": "contract", "ref": "openapi", "detail": "实时抓取"}
        ]
        cases_payload = [
            {
                "code": c.id,
                "title": c.title,
                "category": c.category,
                "module": plan.module,
                "source_req": source_req,
                "input": c.input,
                "expected_error": "",
                "expected_fields": {},
                "patches": [],
                "covers": c.covers,
                "confidence": 0.98,
            }
            for c in plan.cases
        ]
        yield {
            "stage": "codegen",
            "message": f"生成 pytest 完成：{len(plan.cases)} 用例 / {src.count(chr(10))} 行",
            "progress": 0.6,
            "payload_json": json.dumps({"cases": cases_payload, "code": src}, ensure_ascii=False),
        }
        yield {
            "stage": "result",
            "message": f"生成完成：{len(cases_payload)} 条用例待执行",
            "progress": 1.0,
            "payload_json": json.dumps({"gen_code": gen_code, "target": target, "layer": layer, "cases": cases_payload, "code": src, "citations": web_citations}, ensure_ascii=False),
        }
        from app.core.trace import emit

        emit("生成", "testgen-svc", f"Web 层生成完成 {gen_code} layer={layer} 用例={len(cases_payload)}", req_code=source_req, trace_id=trace_id or None)
        return

    fn = _load_fn(repo_id, target, module)
    if fn is None:
        raise LookupError(f"函数未索引: {target}")

    # ① 上下文组装（六路，优先级写死）
    ctx = build_context(repo_id, target, module)
    yield {
        "stage": "plan",
        "message": f"上下文组装: {'>'.join(ctx.sources) or '仅签名'}",
        "progress": 0.08,
        "payload_json": json.dumps({"ctx_sources": ctx.sources}, ensure_ascii=False),
    }

    # ② 阶段 A：用例清单（schema 校验）
    options = {"min_cases": min_cases or 9, "repair": repair}
    plan = plan_cases(fn, target, layer, options)
    yield {
        "stage": "plan",
        "message": f"用例清单 {len(plan.cases)} 条（{', '.join(sorted(plan.categories))}）",
        "progress": 0.2,
        "payload_json": plan.model_dump_json(),
    }

    # ③ 覆盖守卫：静态检查表，缺类自动补
    plan, report = guard(plan, fn)
    yield {
        "stage": "guard",
        "message": f"守卫检查: 检查表 {len(report['checked'])} 项，自动补 {len(report['added'])} 条",
        "progress": 0.35,
        "payload_json": json.dumps(report, ensure_ascii=False),
    }

    # ③b 探针捕获：期望值来自对真实代码的实际执行（现实即规格）。
    # 非精选靶标开启覆写——LLM/守卫的推测断言一律以真实执行结果校正。
    from app.services.testgen.probe import fill_probe_expectations

    plan, probe_note = fill_probe_expectations(
        plan, _repo_checkout(repo_id), overwrite=fn.name not in ("create_order", "sanitize_text")
    )
    if probe_note:
        yield {"stage": "probe", "message": f"探针期望值: {probe_note}", "progress": 0.4, "payload_json": ""}

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
            "source_req": source_req,
            "input": c.input,
            "expected_error": c.expected_error,
            "expected_fields": c.expected_fields,
            "patches": [p.model_dump() for p in c.patches],
            "covers": c.covers,
            "confidence": conf,
        }
        for c, conf in zip(plan.cases, _confidence(plan.cases))
    ]
    yield {
        "stage": "codegen",
        "message": f"生成 pytest 完成：{len(plan.cases)} 用例 / {src.count(chr(10))} 行",
        "progress": 0.6,
        "payload_json": json.dumps({"cases": cases_payload, "code": src}, ensure_ascii=False),
    }

    # ⑤ 结果事件（SANDBOX/COVERAGE 事件由 runner/gateway 阶段补齐；citations 全链路可回溯）
    yield {
        "stage": "result",
        "message": f"生成完成：{len(cases_payload)} 条用例待执行",
        "progress": 1.0,
        "payload_json": json.dumps(
            {"gen_code": gen_code, "target": target, "layer": layer, "cases": cases_payload, "code": src, "citations": ctx.citation_dicts()},
            ensure_ascii=False,
        ),
    }
    from app.core.trace import emit

    emit("生成", "testgen-svc", f"生成完成 {gen_code} 目标={target} 用例={len(cases_payload)} 守卫补={len(report['added'])}", req_code=source_req, trace_id=trace_id or None)


def regenerate_affected(
    repo_id: int,
    target_function: str,
    trace_id: str = "",
    reason: str = "",
    source_req: str = "",
) -> dict:
    """定向重生成（修复回填/契约影响分析）：按目标函数重建清单与代码。

    与首生成同一现实基准：探针重新捕获期望值后渲染，返回完整测试文件源码（code_file），
    由 runner 写回沙箱工作区——修复不是重试，而是以真实行为为准重写断言。
    """
    from app.services.testgen.codegen import codegen
    from app.services.testgen.guard import guard
    from app.services.testgen.planner import plan_cases
    from app.services.testgen.probe import fill_probe_expectations

    target = target_function
    if not target:
        raise ValueError("target_function 必填（修复回填需要明确被测目标）")
    fn = _load_fn(repo_id, target, "")
    if fn is None:
        raise LookupError(f"重生成目标未索引: {target}")
    plan = plan_cases(fn, target, "ut", {"repair": True})
    plan, report = guard(plan, fn)
    plan, _note = fill_probe_expectations(
        plan, _repo_checkout(repo_id), overwrite=fn.name not in ("create_order", "sanitize_text")
    )
    gen_code = f"REPAIR-{(trace_id or 'NA')[-6:]}"
    src = codegen(plan, gen_code)
    from app.core.trace import emit

    emit(
        "生成",
        "testgen-svc",
        f"定向重生成 {len(plan.cases)} 条（原因: {reason or 'repair'}，探针重捕获期望值）",
        req_code=source_req,
        trace_id=trace_id or None,
    )
    return {
        "generation_id": gen_code,
        "cases_total": len(plan.cases),
        "guard_added": len(report["added"]),
        "case_ids": [c.id for c in plan.cases],
        "code_file": src,
    }
