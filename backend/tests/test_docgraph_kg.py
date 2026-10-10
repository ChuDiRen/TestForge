"""文档知识图谱（T8）+ 文档状态（T12）：JSON 容错解析 / 实体合并 / 选择性删除 / 状态聚合。"""

import json
import uuid


def test_parse_json_tolerates_fences_and_noise():
    from services.shared.docgraph import _parse_json

    assert _parse_json('```json\n{"entities": []}\n```') == {"entities": []}
    assert _parse_json('前置说明 {"entities": [{"name": "x"}]} 后缀') == {"entities": [{"name": "x"}]}
    assert _parse_json("not json at all") == {}
    assert _parse_json('{"broken": ') == {}


def test_doc_hash_stable():
    from services.shared.docgraph import _doc_hash

    assert _doc_hash("t", "c") == _doc_hash("t", "c")
    assert _doc_hash("t", "c") != _doc_hash("t", "c2")


def test_apply_extraction_merge_votes_weights_and_selective_delete():
    """v2 合并（LightRAG 三阶段）：type 投票、关系 weight 证据计数、选择性删除。"""
    from services.shared.db import get_session, init_db
    from services.shared.kg_merge import apply_extraction, remove_source
    from services.shared.models import KgEntity, KgExtraction, KgRelation

    init_db()
    rid = 991001
    try:
        apply_extraction(rid, "", "wiki:1", "t", {
            "entities": [
                {"name": "支付服务", "type": "module", "description": "支付域"},
                {"name": "幂等性", "type": "concept", "description": "重复请求防护"},
            ],
            "relations": [{"src": "支付服务", "dst": "幂等性", "type": "依赖", "description": "回调依赖幂等"}],
        }, content_hash="h1", reindex=False)
        with get_session() as sess:
            names = {e.name: e for e in sess.query(KgEntity).filter(KgEntity.repo_id == rid).all()}
            assert set(names) == {"支付服务", "幂等性"}
            rels = sess.query(KgRelation).filter(KgRelation.repo_id == rid).all()
            assert len(rels) == 1 and rels[0].weight == 1.0 and json.loads(rels[0].source_refs) == ["wiki:1"]
            ext = sess.query(KgExtraction).filter(KgExtraction.repo_id == rid).count()
            assert ext == 1, "抽取结果应留存（删除重建依据）"

        # wiki:2 再次抽到 支付服务（type 投票一致）+ 新关系 → weight=2 证据计数
        apply_extraction(rid, "", "wiki:2", "t2", {
            "entities": [{"name": "支付服务", "type": "module", "description": "支付域"}],
            "relations": [{"src": "支付服务", "dst": "幂等性", "type": "依赖", "description": "二文档再次证实"}],
        }, content_hash="h2", reindex=False)
        with get_session() as sess:
            e = sess.query(KgEntity).filter(KgEntity.repo_id == rid, KgEntity.name == "支付服务").first()
            assert sorted(json.loads(e.source_refs)) == ["wiki:1", "wiki:2"], "实体引用应合并"
            assert json.loads(e.etype_votes) == {"module": 2}, "type 投票应累计"
            rel = sess.query(KgRelation).filter(KgRelation.repo_id == rid).first()
            assert rel.weight == 2.0, "关系 weight 应为证据计数"

        # wiki:1 重建后：幂等性仍被 wiki:2 的边引用（LightRAG 语义：边端点是节点的引用者），存活
        apply_extraction(rid, "", "wiki:1", "t", {
            "entities": [{"name": "支付服务", "type": "module", "description": "支付域"}],
            "relations": [],
        }, content_hash="h1b", reindex=False)
        with get_session() as sess:
            left = {e.name for e in sess.query(KgEntity).filter(KgEntity.repo_id == rid).all()}
            assert left == {"支付服务", "幂等性"}, "被剩余边引用的端点应存活"
            rel = sess.query(KgRelation).filter(KgRelation.repo_id == rid).first()
            assert json.loads(rel.source_refs) == ["wiki:2"] and rel.weight == 1.0, "wiki:1 的旧关系证据应撤除"

        # remove_source（删除文档 → 剩余抽取重建）：wiki:2 撤除后，边消失、幂等性（仅靠边引用）随之归零；
        # 支付服务仍被 wiki:1 引用，存活
        remove_source(rid, "", "wiki:2", reindex=False)
        with get_session() as sess:
            left = {e.name for e in sess.query(KgEntity).filter(KgEntity.repo_id == rid).all()}
            assert left == {"支付服务"}, "仅靠 wiki:2 边引用的幂等性应删除，支付服务仍被 wiki:1 引用应存活"
            assert sess.query(KgRelation).filter(KgRelation.repo_id == rid).count() == 0
            assert sess.query(KgExtraction).filter(KgExtraction.repo_id == rid).count() == 1, "wiki:1 的抽取留存仍在（它没被删）"

        # 工作区隔离：同 repo_id 不同 workspace 实体互不可见
        apply_extraction(0, "ws-a", "kgdoc:a1", "ta", {"entities": [{"name": "共享名", "type": "concept", "description": "A 区"}]}, content_hash="ha", reindex=False)
        apply_extraction(0, "ws-b", "kgdoc:b1", "tb", {"entities": [{"name": "共享名", "type": "module", "description": "B 区"}]}, content_hash="hb", reindex=False)
        with get_session() as sess:
            ws_rows = sess.query(KgEntity).filter(KgEntity.name == "共享名").all()
            assert {r.workspace for r in ws_rows} == {"ws-a", "ws-b"}, "工作区应隔离同名词"
    finally:
        with get_session() as sess:
            sess.query(KgEntity).filter(KgEntity.repo_id == rid).delete(synchronize_session=False)
            sess.query(KgRelation).filter(KgRelation.repo_id == rid).delete(synchronize_session=False)
            sess.query(KgExtraction).filter(KgExtraction.repo_id == rid).delete(synchronize_session=False)
            sess.query(KgEntity).filter(KgEntity.name == "共享名").delete(synchronize_session=False)
            sess.query(KgExtraction).filter(KgExtraction.source_ref.in_(["kgdoc:a1", "kgdoc:b1"])).delete(synchronize_session=False)
            sess.commit()
        from services.shared.rag import remove_documents_by_prefix

        remove_documents_by_prefix(f"kg_entity:{rid}:")
        remove_documents_by_prefix(f"kg_relation:{rid}:")


def test_kg_query_empty_graph_safe():
    from services.shared.docgraph import kg_query

    res = kg_query(991999, "完全不存在的主题", mode="mix")
    assert res["mode"] == "mix" and res["entities"] == [] and isinstance(res["rendered"], str)


def test_docstatus_mark_and_summary():
    from services.shared.db import get_session, init_db
    from services.shared.docstatus import mark, summary
    from services.shared.models import DocStatus

    init_db()
    rid = 991002
    key = f"doc:{uuid.uuid4().hex[:8]}"
    try:
        mark(rid, "kg", key, "ok", detail="first")
        mark(rid, "kg", key, "failed", error="boom")  # 同键覆盖
        with get_session() as sess:
            row = sess.query(DocStatus).filter(DocStatus.repo_id == rid, DocStatus.doc_key == key).first()
            assert row is not None and row.status == "failed" and "boom" in row.error
        s = summary(rid)
        assert s["by_kind"].get("kg", {}).get("failed") == 1
        assert any(f["doc_key"] == key for f in s["recent_failures"])
    finally:
        with get_session() as sess:
            sess.query(DocStatus).filter(DocStatus.repo_id == rid).delete(synchronize_session=False)
            sess.commit()
