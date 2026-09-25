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


def test_mock_llm_schema_validation():
    from pydantic import BaseModel

    from services.shared.llm import LLMClient

    class Plan(BaseModel):
        cases: list[str]

    llm = LLMClient()
    assert llm.is_mock
    out = llm.chat_json([], schema=Plan, mock={"cases": ["a", "b"]})
    assert isinstance(out, Plan) and out.cases == ["a", "b"]


def test_mock_llm_rejects_bad_schema():
    from pydantic import BaseModel

    from services.shared.llm import LLMClient, LLMError

    class Plan(BaseModel):
        n: int

    llm = LLMClient()
    with pytest.raises(LLMError):
        llm.chat_json([], schema=Plan, mock={"n": "not-an-int"})


def test_db_init_and_models():
    from sqlalchemy import inspect

    from services.shared.db import get_engine, init_db

    init_db()
    tables = set(inspect(get_engine()).get_table_names())
    for t in ("repos", "functions", "call_edges", "wiki_pages", "wiki_deps", "contracts",
              "contract_diffs", "requirements", "cases", "runs", "defects", "iterations",
              "trace_events", "generations", "generation_events"):
        assert t in tables, f"缺表: {t}"
