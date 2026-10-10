"""符号上下文包（T7）：token 预算裁剪顺序 / citations 去重 / 渲染优先级。"""

from app.services.testgen.context import Citation, ContextBundle, estimate_tokens


def _bundle(big: str = "word " * 400) -> ContextBundle:
    return ContextBundle(
        code="def fn(): pass",
        contract="openapi: 3.0",
        wiki=big,
        kg="kg 知识",
        trace="trace 采样",
        similar="CASE-1 相似用例",
        bugs="BUG-1 缺陷",
        citations=[Citation("code", "a.py:1"), Citation("code", "a.py:1", "dup"), Citation("bugs", "BUG-1")],
    )


def test_estimate_tokens_cjk_vs_ascii():
    assert estimate_tokens("") == 0
    assert estimate_tokens("订单创建") == 4, "CJK 每字约 1 token"
    assert estimate_tokens("abcdefgh") == 2, "拉丁约 4 字符 1 token"


def test_budget_trims_low_priority_first():
    big = "x" * 4000
    ctx = ContextBundle(code="code", contract="contract", wiki=big, similar=big, bugs=big)
    total = ctx.enforce_budget(budget=300)
    assert total <= 300
    assert not ctx.bugs, "最低优先级 bugs 先清"
    assert not ctx.similar, "similar 次之"
    assert ctx.code == "code" and ctx.contract == "contract", "GROUND TRUTH 不清空"


def test_budget_ground_truth_truncate_only():
    big_code = "line\n" * 3000  # ~3000 tokens
    ctx = ContextBundle(code=big_code)
    ctx.enforce_budget(budget=200)
    assert estimate_tokens(ctx.code) <= 400, "code 只截断不清空"
    assert ctx.code, "code 不得为空"


def test_render_priority_order():
    ctx = ContextBundle(code="C", contract="K", wiki="W", trace="T", similar="S", bugs="B", kg="G")
    out = ctx.render()
    assert out.index("code (GROUND TRUTH)") < out.index("contract (GROUND TRUTH)") < out.index("wiki") < out.index("kg") < out.index("trace") < out.index("similar") < out.index("bugs")


def test_citations_dedup_preserving_order():
    ctx = _bundle()
    cites = ctx.citation_dicts()
    refs = [c["ref"] for c in cites]
    assert len(refs) == len(set(refs)), "引用必须按 ref 去重"
    assert refs[0] == "a.py:1"
    assert {"kind", "ref", "detail"} == set(cites[0].keys())
