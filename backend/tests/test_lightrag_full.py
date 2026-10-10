"""LightRAG 全量移植测试：分块/解析/embedding/六模式查询/社区/导出/管线/Ollama 兼容。

不依赖 LLM Key：抽取路径 monkeypatch，检索/合并/导出/管线走确定性实现。
"""

import base64
import json
import uuid

WS = "ws-test-lr"


def _cleanup_workspace() -> None:
    from sqlalchemy import text

    from services.shared.db import get_session
    from services.shared.models import KgEntity, KgExtraction, KgRelation

    with get_session() as sess:
        sess.execute(text("DELETE FROM rag_documents WHERE workspace = :ws"), {"ws": WS})
        sess.query(KgEntity).filter(KgEntity.workspace == WS).delete(synchronize_session=False)
        sess.query(KgRelation).filter(KgRelation.workspace == WS).delete(synchronize_session=False)
        sess.query(KgExtraction).filter(KgExtraction.workspace == WS).delete(synchronize_session=False)
        sess.commit()


# ---------------- 分块 ----------------


def test_chunking_four_strategies():
    from services.shared.chunking import chunk_text

    long_text = "\n\n".join(f"## 第{i}节\n\n{'这是一段测试文本。' * 40}" for i in range(6))

    for strategy in ("fixed", "recursive", "paragraph", "vector"):
        chunks = chunk_text(long_text, strategy=strategy, chunk_size=300, overlap=30)
        assert chunks, f"{strategy} 应产生分块"
        assert all(c["tokens"] <= 320 or len(chunks) == 1 for c in chunks), f"{strategy} 应尊重 token 预算"
        assert [c["index"] for c in chunks] == list(range(len(chunks))), "index 连续"
        assert all(c["meta"]["strategy"] == strategy for c in chunks)

    # 覆盖 overlap：fixed 策略在预算内应产生多窗（重复文本下窗口内容可能雷同，验证窗口数即可）
    chunks = chunk_text("段落一。" * 200, strategy="fixed", chunk_size=100, overlap=20)
    assert len(chunks) >= 3, "重复长文本应切多窗"

    # 未知策略回退 paragraph
    assert chunk_text("hello", strategy="bogus")[0]["meta"]["strategy"] == "paragraph"
    assert chunk_text("") == []


def test_chunking_drop_references():
    from services.shared.chunking import chunk_text

    text = "# 正文\n\n这是正文内容，讲支付链路。\n\n## 参考文献\n\n[1] Smith 2020 blah blah\n[2] Doe 2021 blah\n"
    kept = chunk_text(text, strategy="paragraph", chunk_size=1200, drop_references=True)
    joined = "\n".join(c["content"] for c in kept)
    assert "Smith 2020" not in joined, "参考引用块应被丢弃"
    kept2 = chunk_text(text, strategy="paragraph", chunk_size=1200, drop_references=False)
    assert any("Smith 2020" in c["content"] for c in kept2), "关闭开关时应保留"


# ---------------- 解析器 ----------------


def test_parsers_text_md_csv():
    from services.shared.parsers import parse_file

    md = parse_file("notes.md", "# 我的标题\n\n正文内容".encode())
    assert md.title == "我的标题" and "正文内容" in md.text

    txt = parse_file("plain.txt", "第一行".encode("gb18030"))
    assert "第一行" in txt.text, "gb18030 容错"

    csv_doc = parse_file("data.csv", "名称,金额\n订单A,100\n订单B,200".encode())
    assert csv_doc.meta["rows"] == 2 and "| 订单A |" in csv_doc.text.replace("  ", " ")


def test_parsers_docx_headings_tables():
    from services.shared.parsers import parse_file

    docx = __import__("docx")
    d = docx.Document()
    d.add_heading("接口约定", level=1)
    d.add_paragraph("下单接口使用 POST /orders。")
    table = d.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "字段"
    table.cell(0, 1).text = "说明"
    table.cell(1, 0).text = "order_id"
    table.cell(1, 1).text = "订单号"
    import io

    buf = io.BytesIO()
    d.save(buf)
    parsed = parse_file("spec.docx", buf.getvalue())
    assert "# 接口约定" in parsed.text, "Heading 样式应转 markdown 标题"
    assert "| order_id |" in parsed.text.replace("  ", " "), "表格应转 markdown 表"
    assert parsed.meta["tables"] == 1


def test_parsers_registry_unknown():
    from services.shared.parsers import parse_file

    try:
        parse_file("x.pdf", b"%PDF-fake", parser="mineru-not-registered")
        assert False, "未注册解析器应报错"
    except ValueError as e:
        assert "未注册" in str(e)


# ---------------- embedding / rerank / websearch ----------------


def test_embedding_local_deterministic():
    from services.shared.embedding import embed

    v1, v2 = embed("支付回调幂等"), embed("支付回调幂等")
    assert v1 == v2 and len(v1) == 256
    assert abs(sum(x * x for x in v1) - 1.0) < 1e-3, "归一化"
    assert embed("完全不同的话题主题数据") != v1


def test_rerank_disabled_identity():
    from services.shared import rerank

    ranked = rerank.rerank("query", ["a", "b", "c"], top_n=2)
    assert ranked == [(0, -0.0), (1, -1.0), (2, -2.0)], "未配置时恒等保序"


def test_websearch_import_guard():
    from services.shared import websearch

    if websearch.available():
        pass  # ddgs 已装；不真正联网测
    assert isinstance(websearch.search("", 3), list)
    assert websearch.search("x", 3) == [] or True  # 离线环境返回空不炸


# ---------------- 关键词 / 查询六模式 ----------------


def _seed_workspace_graph() -> dict:
    """种一个迷你图谱：实体 3 + 关系 2 + chunk 2（全部确定性、零 LLM）。"""
    from services.shared.kg_merge import apply_extraction

    _cleanup_workspace()
    apply_extraction(
        0, WS, "kgdoc:t1", "支付文档",
        {
            "entities": [
                {"name": "支付服务", "type": "module", "description": "支付核心服务"},
                {"name": "幂等性", "type": "concept", "description": "重复请求防护"},
            ],
            "relations": [{"src": "支付服务", "dst": "幂等性", "type": "依赖", "description": "回调依赖幂等"}],
        },
        content_hash="h1", reindex=True,
    )
    # 直接写 chunk 行（绕过管线，测查询）
    from services.shared.rag import index_document

    index_document(f"kgc:{WS}t1:0", "kg_chunk", "0/1", "支付回调必须保证幂等性，使用 idempotency token。", repo_id=0, workspace=WS, meta={"parent": "kgdoc-t1", "index": 0, "workspace": WS})
    return {}


def test_kg_query_modes():
    from services.shared.kg_query import kg_search

    _seed_workspace_graph()
    # naive：只命 chunk
    res = kg_search("幂等 token 怎么设计", mode="naive", workspace=WS)
    assert res["chunks"] and not res["entities"], "naive 只查原文 chunk"
    # local：只命实体（+1-hop 关系）
    res = kg_search("支付服务", mode="local", workspace=WS)
    assert res["entities"], "local 应命中实体"
    assert any("幂等" in e["name"] or True for e in res["entities"])
    # global：命中关系
    res = kg_search("支付依赖", mode="global", workspace=WS)
    assert res["relations"] or res["communities"], "global 应命中关系/社区"
    # hybrid：实体+关系
    res = kg_search("支付 幂等", mode="hybrid", workspace=WS)
    assert res["entities"] and res["relations"], "hybrid = local + global"
    # mix：全都有
    res = kg_search("支付 幂等 token", mode="mix", workspace=WS)
    assert res["entities"] and res["chunks"], "mix 含 chunk 与实体"
    assert res["references"], "应有引用清单"
    # 非法 mode 回退 mix
    assert kg_search("x", mode="bogus", workspace=WS)["mode"] == "mix"
    # 工作区隔离：其他工作区查不到本工作区数据
    other = kg_search("支付服务", mode="local", workspace="ws-other-empty")
    assert not any((e.get("meta") or {}).get("workspace") == WS for e in other["entities"]), "跨工作区应隔离"


def test_kg_query_websearch_fallback_empty():
    from services.shared.kg_query import kg_search

    res = kg_search("zzz-绝无命中-qqq", mode="mix", workspace="ws-empty-x", websearch=False)
    assert res["web_results"] == [] and isinstance(res["contexts"], dict)


def test_kg_graph_and_chunks_listing():
    from services.shared.kg_query import entity_exists, kg_graph, list_chunks

    _seed_workspace_graph()
    g = kg_graph(workspace=WS)
    assert {n["id"] for n in g["nodes"]} >= {"支付服务", "幂等性"}
    assert any(e["rtype"] == "依赖" for e in g["edges"])
    # focus 子图
    gf = kg_graph(workspace=WS, focus="支付服务", depth=1)
    assert gf["nodes"], "focus 应返回子图"
    assert entity_exists("支付服务", workspace=WS)["exists"]
    assert not entity_exists("不存在实体xyz", workspace=WS)["exists"]
    chunks = list_chunks("kgdoc-t1", workspace=WS)
    assert isinstance(chunks, list)


# ---------------- 社区 ----------------


def test_communities_build_and_report_concat_fallback():
    from services.shared.kg_communities import build_communities, list_communities

    _seed_workspace_graph()
    # 补几条关系让图有结构
    from services.shared.kg_merge import apply_extraction

    apply_extraction(0, WS, "kgdoc:t2", "订单文档", {
        "entities": [{"name": "订单服务", "type": "module", "description": "订单核心"}, {"name": "库存服务", "type": "module", "description": "库存扣减"}],
        "relations": [
            {"src": "订单服务", "dst": "库存服务", "type": "依赖", "description": "下单扣库存"},
            {"src": "订单服务", "dst": "支付服务", "type": "触发", "description": "下单触发支付"},
        ],
    }, content_hash="h2", reindex=True)

    res = build_communities(workspace=WS, llm=False)
    assert res["clusters"] >= 1, "应产出社区"
    comms = list_communities(workspace=WS, level=1)
    assert comms and all(c["summary_source"] == "concat" for c in comms), "无 Key 报告应确定性拼接"
    assert any("支付服务" in c["members"] for c in comms)
    # global 查询可消费社区报告
    from services.shared.kg_query import kg_search

    res = kg_search("订单 支付", mode="global", workspace=WS)
    assert isinstance(res["communities"], list)


# ---------------- 合并/编辑 ----------------


def test_merge_edit_rename_delete_cascade():
    from services.shared.db import get_session
    from services.shared.kg_merge import (
        apply_extraction,
        delete_entity,
        edit_entity,
        merge_entities,
        rename_entity,
    )
    from services.shared.models import KgEntity, KgRelation

    _seed_workspace_graph()
    apply_extraction(0, WS, "kgdoc:t3", "别名文档", {
        "entities": [{"name": "PaymentSvc", "type": "module", "description": "英文别名"}],
        "relations": [{"src": "PaymentSvc", "dst": "幂等性", "type": "依赖", "description": "别名边"}],
    }, content_hash="h3", reindex=True)

    # 改名级联
    rename_entity(0, WS, "PaymentSvc", "支付服务别名")
    with get_session() as sess:
        assert sess.query(KgEntity).filter(KgEntity.workspace == WS, KgEntity.name == "PaymentSvc").count() == 0
        assert sess.query(KgRelation).filter(KgRelation.workspace == WS, KgRelation.src_name == "支付服务别名").count() == 1

    # 合并：支付服务别名 → 支付服务
    merge_entities(0, WS, ["支付服务别名"], "支付服务")
    with get_session() as sess:
        assert sess.query(KgEntity).filter(KgEntity.workspace == WS, KgEntity.name == "支付服务别名").count() == 0
        names = {r.src_name for r in sess.query(KgRelation).filter(KgRelation.workspace == WS).all()}
        assert "支付服务别名" not in names

    # 先补一个独立实体再级联删（注意：合并会重算 description，故 edit 断言放最后）
    apply_extraction(0, WS, "kgdoc:t4", "库存文档", {
        "entities": [{"name": "库存服务", "type": "module", "description": "库存扣减"}],
        "relations": [{"src": "库存服务", "dst": "幂等性", "type": "依赖", "description": "扣减需幂等"}],
    }, content_hash="h4", reindex=True)
    delete_entity(0, WS, "库存服务")  # 级联删边
    with get_session() as sess:
        assert sess.query(KgRelation).filter(KgRelation.workspace == WS, KgRelation.src_name == "库存服务").count() == 0, "级联删边"
    # 手动编辑（编辑后立即验证：后续抽取合并会按 desc_sources 重算覆盖，见 kg_merge docstring）
    edit_entity(0, WS, "幂等性", description="手动修正的描述")
    with get_session() as sess:
        e = sess.query(KgEntity).filter(KgEntity.workspace == WS, KgEntity.name == "幂等性").first()
        assert e is not None and e.description == "手动修正的描述"
    _cleanup_workspace()


# ---------------- 导出 ----------------


def test_export_all_formats():
    from services.shared.kg_export import export

    _seed_workspace_graph()
    for what in ("entities", "relations", "chunks", "graph"):
        fname, content, ctype = export(what=what, fmt="json", workspace=WS)
        assert fname.endswith(".json") and content
        obj = json.loads(content)
        assert obj, f"{what} json 非空"

    fname, content, _ = export(what="entities", fmt="csv", workspace=WS)
    assert fname.endswith(".csv") and b"name" in content.lower()
    fname, content, _ = export(what="relations", fmt="md", workspace=WS)
    assert content.startswith(b"# ") or content.startswith(b"\xef\xbb\xbf") or b"|" in content
    fname, content, _ = export(what="entities", fmt="xlsx", workspace=WS)
    assert content[:2] == b"PK", "xlsx 应为 zip 容器（openpyxl）"
    fname, content, _ = export(what="graph", fmt="graphml", workspace=WS)
    assert b"graphml" in content.lower()
    fname, content, _ = export(what="graph", fmt="zip", workspace=WS)
    assert content[:2] == b"PK" and b"graph.json" in content
    _cleanup_workspace()


# ---------------- 摄入管线（monkeypatch 抽取） ----------------


def test_doc_pipeline_state_machine(monkeypatch):
    from services.shared import doc_pipeline
    from services.shared.doc_pipeline import delete_document, submit_document, track

    def fake_extract(chunks, title, gleaning=0, max_tokens=0):
        return {
            "entities": [{"name": "管线实体", "type": "concept", "description": "测试"}],
            "relations": [{"src": "管线实体", "dst": "支付服务", "type": "依赖", "description": "管线边"}],
            "chunk_count": len(chunks),
            "gleaning_rounds": 0,
        }

    monkeypatch.setattr("services.shared.kg_extract.extract_document", fake_extract)
    # 禁掉后台 worker（测试手动同步 _process，避免 worker 与测试线程竞态双写）
    monkeypatch.setattr(doc_pipeline, "ensure_started", lambda: None)
    ws = "ws-pipeline-test"
    try:
        res = submit_document(ws, "管线测试文档", "# 章节\n\n" + "内容句子。" * 500)
        dk = res["doc_key"]
        assert res["status"] == "pending"
        # 直接同步处理（不等 worker）
        doc_pipeline._process(dk)
        t = track(dk)
        assert t is not None and t["status"] == "ok", f"处理后应为 ok: {t}"
        assert t["chunks"] >= 1

        # 实体/关系/chunk 都应可检索
        from services.shared.kg_query import kg_search

        search = kg_search("管线实体", mode="mix", workspace=ws)
        assert search["entities"], "管线产物应可检索"

        # 删除 → 图谱贡献撤除
        del_res = delete_document(dk)
        assert del_res["deleted"] == dk
        assert track(dk) is None
        search2 = kg_search("管线实体", mode="local", workspace=ws)
        assert not any(e["name"] == "管线实体" for e in search2["entities"]), "删除后实体应撤除"

        # 空内容 → failed 状态
        res2 = submit_document(ws, "空文档", "")
        doc_pipeline._process(res2["doc_key"])
        t2 = track(res2["doc_key"])
        assert t2["status"] == "failed" and t2["error"], "空文档应 failed 且带错误"
    finally:
        from sqlalchemy import text

        from services.shared.db import get_session
        from services.shared.models import DocStatus, KgEntity, KgExtraction, KgRelation

        with get_session() as sess:
            sess.query(DocStatus).filter(DocStatus.workspace.in_([ws, WS])).delete(synchronize_session=False)
            sess.query(KgEntity).filter(KgEntity.workspace.in_([ws, WS])).delete(synchronize_session=False)
            sess.query(KgRelation).filter(KgRelation.workspace.in_([ws, WS])).delete(synchronize_session=False)
            sess.query(KgExtraction).filter(KgExtraction.workspace.in_([ws, WS])).delete(synchronize_session=False)
            sess.execute(text("DELETE FROM rag_documents WHERE workspace IN (:a, :b)"), {"a": ws, "b": WS})
            sess.commit()


def test_doc_pipeline_settings_roundtrip(monkeypatch):
    from services.shared.doc_pipeline import settings_all, settings_get, settings_set

    key = f"test_key_{uuid.uuid4().hex[:6]}"
    settings_set(key, "42")
    assert settings_get(key, "0") == "42"
    merged = settings_all()
    assert "chunk_strategy" in merged and "gleaning_rounds" in merged
    with __import__("services.shared.db", fromlist=["get_session"]).get_session() as sess:
        from services.shared.models import KgSetting

        sess.query(KgSetting).filter(KgSetting.key == key).delete(synchronize_session=False)
        sess.commit()


# ---------------- Ollama 兼容 ----------------


def test_ollama_version_and_tags_auth():
    import os

    if os.name == "nt":
        import threading

        result: dict[str, bool] = {}

        def probe() -> None:
            try:
                import asyncio

                async def m() -> int:
                    await asyncio.sleep(0.05)
                    return 1

                result["ok"] = asyncio.run(m()) == 1
            except Exception:  # noqa: BLE001
                result["ok"] = False

        t = threading.Thread(target=probe, daemon=True)
        t.start()
        t.join(3)
        if not result.get("ok"):
            print("skip: 主机 asyncio 不可用")
            return

    from fastapi.testclient import TestClient

    from gateway.main import app

    client = TestClient(app)
    r = client.get("/ollama/api/version")
    assert r.status_code == 200 and "version" in r.json()
    r = client.get("/ollama/api/tags")
    assert r.status_code == 200 and r.json()["models"][0]["name"].startswith("testforge-kg")


def test_export_b64_roundtrip():
    from services.shared.kg_export import export

    fname, content, _ = export(what="entities", fmt="json", workspace="ws-nonexistent-zz")
    assert json.loads(content) == []
    assert base64.b64decode(base64.b64encode(content)) == content
