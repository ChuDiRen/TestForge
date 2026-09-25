"""M0 冒烟：封套 / 脱敏 / trace / mock LLM schema 校验。"""

import pytest


def test_sanitize_masks_credentials():
    from services.shared.sanitize import sanitize_text

    text = 'Authorization: Bearer abc123 and password=hunter2 and key=sk-verysecret123'
    out = sanitize_text(text)
    assert "abc123" not in out and "hunter2" not in out and "verysecret" not in out


def test_trace_id_format():
    from services.shared.logutil import new_trace_id

    tid = new_trace_id()
    assert tid.startswith("tr_") and len(tid) == 15


def test_trace_emit_falls_back_to_db():
    """TraceLog gRPC 不可达时降级直写 DB，事件不丢。"""
    from services.shared import trace

    tid = trace.emit("仓库", "pytest", "emit 冒烟", extra={"k": "v"})
    rows = trace.query(trace_id=tid)
    assert rows and rows[0]["summary"] == "emit 冒烟"


def test_deepagent_rejects_missing_key(monkeypatch):
    """无 mock 实现：未配置 DeepSeek Key 时智能体规划必须显式报错。"""
    from services.shared.config import get_settings
    from services.shared.llm import LLMError
    from services.testgen_svc.agent import plan_with_deepagent
    from services.testgen_svc.fninfo import parse_signature

    monkeypatch.setattr(get_settings(), "llm_api_key", "")
    fn = parse_signature("def f(x):\n    return x", "f")
    with pytest.raises(LLMError):
        plan_with_deepagent(fn, "f", [], [], [])


def test_plan_schema_coerces_llm_noise():
    """DeepSeek 输出的常见噪声（covers 为列表 / 字段为 null）应被矫正而非整单作废。"""
    from services.testgen_svc.schemas import CasePlan

    plan = CasePlan.model_validate({
        "target": "x.y.f",
        "module": "x.y",
        "cases": [
            {"id": "TC-1", "title": "t", "category": "normal", "input": {"name": "pay_order_1"}, "covers": ["payment 分支", "别名归一"]},
            {"id": None, "title": None, "covers": ["order 分支"], "input": {}},
        ],
    })
    assert plan.cases[0].covers == "payment 分支; 别名归一"
    assert plan.cases[1].id == "" and plan.cases[1].title == "" and plan.cases[1].covers == "order 分支"


def test_db_init_and_models():
    from sqlalchemy import inspect

    from services.shared.db import get_engine, init_db

    init_db()
    tables = set(inspect(get_engine()).get_table_names())
    for t in ("repos", "functions", "call_edges", "wiki_pages", "wiki_deps", "contracts",
              "contract_diffs", "requirements", "cases", "runs", "defects", "iterations",
              "trace_events", "generations", "generation_events"):
        assert t in tables, f"缺表: {t}"
