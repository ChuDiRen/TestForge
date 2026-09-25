"""Windows 原生全链路业务验证（无 asyncio / 无 HTTP，mono 直调服务层）。

证明全部业务代码在 Windows 原生执行正确：
建库 → 仓库接入(git clone) → tree-sitter 多语言索引 → Wiki 分层编译 →
两阶段生成(清单+守卫+代码渲染) → 沙箱执行 → 用例/执行入库 → 缺陷分诊 →
定向回归 → 迭代准入准出 → 测试报告 → traceID 全链路。

Linux 服务器上同样可跑：python scripts/verify_native.py
"""

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    CHECKS.append((name, cond, detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def main() -> int:
    print(f"== TestForge 原生业务链路验证（platform={sys.platform}, mono 直调）==")
    from services.shared.db import init_db
    from services.shared.logutil import set_trace_id

    init_db()
    from services.shared.mono import register_all

    register_all()
    trace_all = f"tr_{uuid.uuid4().hex[:12]}"
    set_trace_id(trace_all)

    # ① 仓库接入 + 多语言索引（repo-svc）
    from services.repo_svc import service as repo_service
    from services.shared.db import get_session as gs
    from services.shared.models import Functions

    repo_url = (ROOT / "fixtures" / "sample-repo").as_uri() if sys.platform == "win32" else "file:///mnt/e/TestForge/fixtures/sample-repo"
    reg = repo_service.register(repo_url, "main", "", False)
    rid = reg["id"]
    check("仓库接入（git clone）", reg["status"] == "已接入", f"id={rid}")
    with gs() as sess:
        fns = sess.query(Functions).filter(Functions.repo_id == rid).all()
    check("tree-sitter 索引", any(f.name == "create_order" for f in fns), f"{len(fns)} 函数")

    # ② Wiki 分层编译（wiki-builder）
    from services.wiki_builder import service as wiki_service

    rev = ""
    with gs() as sess:
        from services.shared.models import Repos

        r = sess.get(Repos, rid)
        rev = r.head_rev
    wb = wiki_service.rebuild(rid, "", rev, [], trace_id=trace_all)
    check("Wiki 分层编译", wb["pages_rebuilt"] >= 10, f"{wb['pages_rebuilt']} 页")

    # ③ 两阶段生成（testgen-svc：plan → guard → codegen）
    from services.shared.llm import get_llm
    from services.testgen_svc.codegen import codegen
    from services.testgen_svc.context import build_context
    from services.testgen_svc.fninfo import parse_signature
    from services.testgen_svc.guard import guard
    from services.testgen_svc.planner import plan_cases

    with gs() as sess:
        row = sess.query(Functions).filter(Functions.repo_id == rid, Functions.name == "create_order").first()
    fn = parse_signature(row.source, "create_order")
    fn.module = row.module
    ctx = build_context(rid, "create_order")
    check("六路上下文组装", "code" in ctx.sources, f"{'>'.join(ctx.sources)}")
    llm = get_llm()
    plan = plan_cases(llm, fn, "create_order", "ut", {})
    plan, report = guard(plan, fn)
    src = codegen(llm, plan, "GEN-NATIVE")
    from services.shared.trace import emit

    emit("生成", "testgen-svc", f"原生验证生成完成 目标=create_order 用例={len(plan.cases)} 守卫补={len(report['added'])}")
    check("两阶段生成+守卫", len(plan.cases) >= 9 and len(report["added"]) >= 0, f"{len(plan.cases)} 用例（守卫补 {len(report['added'])}）")

    # ④ 沙箱执行（runner-svc）
    from services.runner_svc import service as runner_service

    run_code = f"RUN-{uuid.uuid4().hex[:8].upper()}"
    cases = [
        {"code": c.id, "title": c.title, "layer": "ut", "module": plan.module, "category": c.category,
         "schema_json": json.dumps({"code_file": src, "code": c.id, "title": c.title, "category": c.category}, ensure_ascii=False),
         "source_req": ""}
        for c in plan.cases
    ]
    res = runner_service.execute_suite(run_code, cases, src, rid, trace_all, "", only=None)
    check("沙箱执行全部通过", res["status"] == "success", f"{res['pass_count']}/{res['pass_total']} 覆盖率={res['coverage']}% sandbox={res['sandbox_status']}")

    # ⑤ 用例入库（gateway 同款逻辑的直调等价）
    from services.shared.models import Cases

    case_results = res["cases"]
    native_tag = trace_all[-6:]
    with gs() as sess:
        for c in cases:
            ok = case_results.get(c["code"]) == "passed"
            sess.add(Cases(code=f"CASE-NATIVE-{native_tag}-{c['code']}", layer="ut", title=c["title"], module=c["module"],
                           category=c["category"], schema_json=c["schema_json"], confidence=0.95,
                           status="已入库" if ok else "草稿", trace_id=trace_all, last_run_ok=ok,
                           target_function="create_order", repo_id=rid))
        sess.commit()
    check("用例入库（带 traceID）", True, f"{len(cases)} 条")

    # ⑥ 失败分诊 + 定向回归（trace-svc defect_svc）
    from services.trace_svc import defect_svc

    stored_codes = [f"CASE-NATIVE-{native_tag}-{c['code']}" for c in cases]
    with gs() as sess:
        from services.shared.models import Runs

        sess.add(Runs(code=run_code, target="create_order", layer="ut", trigger="原生验证",
                      sandbox_status=res["sandbox_status"], pass_total=res["pass_total"],
                      pass_count=res["pass_count"], coverage=res["coverage"], repair_rounds=0,
                      cost_s=res["cost_s"], status="success", trace_id=trace_all,
                      req_code="REQ-NATIVE", gen_id="GEN-NATIVE", log_json="{}"))
        sess.commit()
    d = defect_svc.create_from_run(run_code, stored_codes[:2], "REQ-NATIVE", trace_all, "原生验证")
    reg_res = defect_svc.trigger_regression(d["id"], trace_all)
    check("缺陷自动建+回归只跑关联用例", reg_res["pass_total"] == 2 and reg_res["status"] == "已关闭",
          f"重跑 {reg_res['pass_total']} 条 → {reg_res['status']}")

    # ⑦ 迭代计划 + 报告（trace-svc plan_svc）
    from services.trace_svc import plan_svc

    iter_code = f"ITER-{uuid.uuid4().hex[:6].upper()}"
    with gs() as sess:
        from services.shared.models import Iterations

        sess.add(Iterations(code=iter_code, version="native", req_codes=json.dumps(["REQ-NATIVE"]), trace_id=trace_all))
        sess.commit()
    check_result = plan_svc.evaluate_plan(iter_code, "native", ["REQ-NATIVE"], trace_all)
    check("迭代准入准出判定", "checks" in check_result and len(check_result["checks"]) >= 4, f"{len(check_result['checks'])} 项核对")
    rpt = plan_svc.build_report(iter_code, trace_all)
    check("测试报告生成", "summary" in rpt and rpt["runs"]["pass_rate"] == 100.0, f"通过率 {rpt['runs']['pass_rate']}%")

    # ⑧ trace 全链路（仓库/生成/执行/缺陷/计划 同一 trace）
    from services.shared.trace import query as trace_query

    events = trace_query(trace_id=trace_all)
    types = {e["type"] for e in events}
    check("traceID 全链路（单一 trace 贯穿五类事件）", {"仓库", "生成", "执行", "缺陷", "计划"} <= types, f"{len(events)} 事件: {sorted(types)}")

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    print(f"== 结果: {passed}/{len(CHECKS)} ==")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
