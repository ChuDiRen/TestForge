"""M1 核心逻辑单测：覆盖守卫 / 代码生成渲染 / junit+coverage 解析 / 契约 diff / RAG。"""

import json

# ---------------- 覆盖守卫（PROMPT §11 必须覆盖） ----------------


def test_guard_adds_missing_permission_category():
    from services.testgen_svc.fninfo import parse_signature
    from services.testgen_svc.guard import guard
    from services.testgen_svc.schemas import CasePlan, PlannedCase

    src = "def pay(user: dict, sku: str, quantity: int):\n    '''下单，权限校验'''\n"
    fn = parse_signature(src, "pay")
    plan = CasePlan(target="pay", module="app.orders.service", cases=[PlannedCase(id="TC-001", title="happy", category="normal", input={"user": {"id": "u"}, "sku": "s", "quantity": 1})])
    out, report = guard(plan, fn)
    cats = {c.category for c in out.cases}
    assert "permission" in cats, "守卫应补齐权限类"
    assert all(c.guard_added for c in out.cases if c.category == "permission")


def test_guard_skips_optional_params():
    from services.testgen_svc.fninfo import parse_signature
    from services.testgen_svc.guard import guard
    from services.testgen_svc.schemas import CasePlan, PlannedCase

    src = "def order(user: dict, sku: str, coupon: str = None):\n    pass\n"
    fn = parse_signature(src, "order")
    plan = CasePlan(target="order", module="m", cases=[PlannedCase(id="TC-001", title="t", category="normal", input={"user": {"id": "u"}, "sku": "s"})])
    out, _ = guard(plan, fn)
    # 可选参数 coupon 不应有 NULL/空/类型错 用例
    coupon_cases = [c for c in out.cases if "coupon" in c.input]
    assert not coupon_cases, "可选参数不应生成 NULL/空/类型错守卫用例"


# ---------------- 阶段 B 代码生成渲染 ----------------


def test_codegen_renders_valid_python():
    import ast

    from services.testgen_svc.codegen import codegen
    from services.testgen_svc.planner import _curated_create_order
    from services.testgen_svc.schemas import CasePlan

    plan = CasePlan(target="create_order", module="app.orders.service", cases=_curated_create_order())
    src = codegen(plan, "GEN-TEST")
    tree = ast.parse(src)  # 语法必须有效
    test_fns = [n.name for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
    assert len(test_fns) == 13
    assert all("-" not in name for name in test_fns), "测试函数名不允许连字符"


# ---------------- junit + 覆盖率解析（PROMPT §11 必须覆盖） ----------------


def test_parse_junit_report(tmp_path):
    from services.runner_svc.sandbox import _parse_junit

    report = tmp_path / "report.xml"
    report.write_text(
        """<testsuites><testsuite tests="3" failures="1" errors="0">
        <testcase name="test_tc001_happy" classname="t"/>
        <testcase name="test_tc002_low" classname="t"/>
        <testcase name="test_tc004_over" classname="t"><failure message="expected">...</failure></testcase>
        </testsuite></testsuites>""",
        encoding="utf-8",
    )
    res = _parse_junit(report, mode="local")
    assert res.pass_total == 3 and res.pass_count == 2
    assert res.cases.get("TC-001") == "passed" and res.cases.get("TC-004") == "failed"
    assert len(res.failures) == 1


# ---------------- 契约 diff breaking 规则 ----------------


def test_contract_diff_breaking_rules():
    from services.contract_registry.service import diff_specs

    v1 = json.dumps({"paths": {"/api/pay": {"post": {"__fields__": {"payUrl": {}, "amount": {"required": True}}}}}, "error_codes": ["PAY_101", "PAY_402"]})
    v2 = json.dumps({"paths": {"/api/pay": {"post": {"__fields__": {"redirectUrl": {"required": True}, "amount": {"required": True}}}}}, "error_codes": ["PAY_402"]})
    changes, breaking = diff_specs(v1, v2, "rest")
    assert breaking
    assert any("payUrl" in c for c in changes)
    assert any("redirectUrl" in c for c in changes)
    assert any("PAY_101" in c for c in changes)


def test_contract_diff_non_breaking_addition():
    from services.contract_registry.service import diff_specs

    v1 = json.dumps({"paths": {"/api/pay": {"post": {"__fields__": {"amount": {"required": True}}}}}, "error_codes": []})
    v2 = json.dumps({"paths": {"/api/pay": {"post": {"__fields__": {"amount": {"required": True}, "memo": {}}}}}, "error_codes": []})
    changes, breaking = diff_specs(v1, v2, "rest")
    assert not breaking and any("memo" in c for c in changes)


# ---------------- RAG embedding 相似性 ----------------


def test_rag_embed_deterministic_and_similar():
    from services.shared.rag import embed

    a1 = embed("create_order 下单数量边界")
    a2 = embed("create_order 下单数量边界")
    b = embed("支付渠道完全不同词汇")
    assert a1 == a2, "同文本向量必须一致（生成可复现）"
    sim = sum(x * y for x, y in zip(a1, embed("create_order 数量上限")))
    assert sim > sum(x * y for x, y in zip(a1, b)), "共享关键词的文本应更相似"


# ---------------- 可测性评分（G0） ----------------


def test_testability_unmeasurable_req_rejected():
    from services.req_svc.parser import extract_rules, testability_score

    bad = "希望系统整体更好用，提升用户体验，尽快优化一下。"
    score = testability_score(bad, extract_rules(bad))
    assert score < 80, "不可测需求必须 <80 自动打回"

    good = "作为买家我希望下单受保护。\n验收条件：数量 1~999 允许下单；禁用用户应被拒绝。"
    score2 = testability_score(good, extract_rules(good))
    assert score2 >= 80
