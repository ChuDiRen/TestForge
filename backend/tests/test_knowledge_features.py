"""跨仓契约匹配（T11）+ RAG 评估（T13）+ MCP 协议（T14）。"""

import io
import json
import uuid

# ---------------- T11 跨仓消费方匹配 ----------------


def test_contract_tokens_extracts_ops():
    from services.contract_registry.service import _contract_tokens

    spec = json.dumps({"paths": {"/api/payments/refund": {"post": {}}}, "rpcs": ["SubmitPayment"], "fields": {"amount": {}}})
    toks = _contract_tokens("payment-contract", spec)
    assert "payment-contract" in toks
    assert "payments" in toks and "refund" in toks
    assert "submitpayment" in toks
    assert "amount" in toks


def test_detect_consumers_cross_repo():
    """消费方源码引用契约词 → 该仓自动登记为消费方（跨仓）。"""
    from services.contract_registry.service import _detect_consumers
    from services.shared.db import get_session, init_db
    from services.shared.models import Functions, Repos

    init_db()
    rid = 992001
    try:
        with get_session() as sess:
            r = Repos(url="https://example.com/consumer-svc.git", branch="main", status="已接入", local_path="/tmp/x")
            sess.add(r)
            sess.flush()
            rid = r.id
            sess.add(Functions(repo_id=rid, module="pay.client", name="call_pay", signature="call_pay()", source="resp = requests.post('/api/payments/refund', json=payload)", file="pay/client.py", line=1, language="python"))
            sess.add(Functions(repo_id=rid, module="misc", name="unrelated", signature="unrelated()", source="return 42", file="misc/a.py", line=1, language="python"))
            sess.commit()
        consumers = _detect_consumers("payment-contract", json.dumps({"paths": {"/api/payments/refund": {"post": {}}}}))
        assert "consumer-svc" in consumers, "引用端点的仓库应被识别为消费方"
    finally:
        with get_session() as sess:
            sess.query(Functions).filter(Functions.repo_id == rid).delete(synchronize_session=False)
            sess.query(Repos).filter(Repos.id == rid).delete(synchronize_session=False)
            sess.commit()


# ---------------- T13 RAG 评估 ----------------


def test_rag_eval_metrics_bounded():
    from services.shared.db import get_session, init_db
    from services.shared.models import Cases
    from services.shared.rag import index_case, remove_document
    from services.shared.rag_eval import evaluate, golden_set

    init_db()
    code = f"CASE-EVL-{uuid.uuid4().hex[:6].upper()}-TC-001"
    try:
        index_case(code, "create_order 下单创建 订单校验 库存", title="下单正常路径", layer="ut", category="normal")
        with get_session() as sess:
            sess.add(Cases(code=code, layer="ut", title="下单正常路径", module="orders", category="normal", target_function="create_order", repo_id=992002, schema_json="{}", status="已入库"))
            sess.commit()
        gs = golden_set(limit=10)
        assert gs, "有已入库用例必须能构建黄金集"
        res = evaluate(k=3, limit=10)
        assert res["queries"] >= 1
        for chan in ("hybrid", "vector_only"):
            assert 0.0 <= res[chan]["recall_at_k"] <= 1.0
            assert 0.0 <= res[chan]["mrr"] <= 1.0
    finally:
        with get_session() as sess:
            row = sess.query(Cases).filter(Cases.code == code).first()
            if row is not None:
                sess.delete(row)
                sess.commit()
        remove_document(f"case:{code}")


# ---------------- T14 MCP 协议 ----------------


def _run_mcp(requests: list[dict], monkeypatch, capsys) -> list[dict]:
    from services import mcp_server

    stdin_lines = "\n".join(json.dumps(r, ensure_ascii=False) for r in requests) + "\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_lines))
    mcp_server.serve()
    out = capsys.readouterr().out
    return [json.loads(ln) for ln in out.splitlines() if ln.strip()]


def test_mcp_initialize_and_tools_list(monkeypatch, capsys):
    replies = _run_mcp(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ],
        monkeypatch,
        capsys,
    )
    assert len(replies) == 2, "通知不回包"
    init = replies[0]
    assert init["result"]["protocolVersion"] and init["result"]["serverInfo"]["name"] == "testforge"
    tools = replies[1]["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"list_functions", "function_impact", "search_cases", "get_symbol_context", "knowledge_query", "repo_status"} <= names
    assert all("inputSchema" in t for t in tools)


def test_mcp_tool_call_roundtrip(monkeypatch, capsys):
    from services.shared.db import init_db

    init_db()
    replies = _run_mcp(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "repo_status", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "knowledge_query", "arguments": {"query": "支付", "mode": "mix"}}},
            {"jsonrpc": "2.0", "id": 3, "method": "bogus/method"},
        ],
        monkeypatch,
        capsys,
    )
    assert len(replies) == 3
    status = json.loads(replies[0]["result"]["content"][0]["text"])
    assert "repos" in status and "ingest" in status
    kg = json.loads(replies[1]["result"]["content"][0]["text"])
    assert kg["mode"] == "mix"
    assert replies[2]["error"]["code"] == -32601


def test_fix_duplicates_cleans_wiki_deps():
    """回归：一键去重删页前必须清 wiki_deps 双向依赖边（page_id / depends_on_page_id 双外键），
    否则触发 wiki_deps_depends_on_page_id_fkey FK violation（线上已炸过）。"""
    import uuid

    from services.shared.db import get_session
    from services.shared.models import Repos, WikiDeps, WikiPages

    marker = uuid.uuid4().hex[:8]
    with get_session() as sess:
        repo = Repos(url=f"https://example.com/dup-{marker}.git", branch="main", status="已接入")
        sess.add(repo)
        sess.flush()
        rid = repo.id
        keep_dup = WikiPages(repo_id=rid, level="function", title="函数 dup", content_md="keep oldest")
        drop_dup = WikiPages(repo_id=rid, level="function", title="函数 dup", content_md="drop later")
        other = WikiPages(repo_id=rid, level="function", title=f"函数 other-{marker}", content_md="unique")
        sess.add_all([keep_dup, drop_dup, other])
        sess.flush()
        # 双向边：被删页的出边 + 保留页指向被删页的入边（后者正是线上 FK 炸点）
        sess.add(WikiDeps(page_id=drop_dup.id, depends_on_page_id=other.id))
        sess.add(WikiDeps(page_id=other.id, depends_on_page_id=drop_dup.id))
        sess.commit()
        ids = {"keep": keep_dup.id, "drop": drop_dup.id, "other": other.id}

    try:
        from gateway.main import wiki_fix_duplicates

        res = wiki_fix_duplicates(rid)
        data = res["data"]
        assert ids["drop"] in data["removed"]
        assert data["kept"] >= 2, "同标题保留最早一页 + 唯一标题页"
        with get_session() as sess:
            alive = {p.id for p in sess.query(WikiPages).filter(WikiPages.repo_id == rid).all()}
            assert ids["drop"] not in alive and {ids["keep"], ids["other"]} <= alive
            edges = sess.query(WikiDeps).filter(
                (WikiDeps.page_id == ids["drop"]) | (WikiDeps.depends_on_page_id == ids["drop"])
            ).count()
            assert edges == 0, "指向被删页的双向依赖边必须一并清理"
    finally:
        from sqlalchemy import text

        from services.shared.rag import remove_document

        with get_session() as sess:
            sess.execute(
                text(
                    "DELETE FROM wiki_deps WHERE page_id IN (SELECT id FROM wiki_pages WHERE repo_id=:r) "
                    "OR depends_on_page_id IN (SELECT id FROM wiki_pages WHERE repo_id=:r)"
                ),
                {"r": rid},
            )
            sess.execute(text("DELETE FROM wiki_pages WHERE repo_id=:r"), {"r": rid})
            sess.execute(text("DELETE FROM repos WHERE id=:r"), {"r": rid})
            sess.commit()
        for pid in ids.values():
            remove_document(f"wiki:{pid}")
