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


def test_apply_doc_merge_and_selective_delete():
    """单文档重建：关系按 source_ref 重建；实体引用合并；只属本文档且消失的实体被删。"""
    from services.shared.db import get_session, init_db
    from services.shared.docgraph import _apply_doc
    from services.shared.models import KgEntity, KgRelation

    init_db()
    rid = 991001
    try:
        _apply_doc(rid, "wiki:1", "h1", {"title": "t"}, {
            "entities": [
                {"name": "支付服务", "type": "module", "description": "支付域"},
                {"name": "幂等性", "type": "concept", "description": "重复请求防护"},
            ],
            "relations": [{"src": "支付服务", "dst": "幂等性", "type": "依赖", "description": "回调依赖幂等"}],
        })
        with get_session() as sess:
            names = {e.name: e for e in sess.query(KgEntity).filter(KgEntity.repo_id == rid).all()}
            assert set(names) == {"支付服务", "幂等性"}
            rels = sess.query(KgRelation).filter(KgRelation.repo_id == rid).all()
            assert len(rels) == 1 and rels[0].source_ref == "wiki:1"

        # wiki:2 只贡献 支付服务 → 实体引用合并为两处
        _apply_doc(rid, "wiki:2", "h2", {"title": "t2"}, {
            "entities": [{"name": "支付服务", "type": "module", "description": "支付域"}],
            "relations": [],
        })
        with get_session() as sess:
            e = sess.query(KgEntity).filter(KgEntity.repo_id == rid, KgEntity.name == "支付服务").first()
            assert sorted(json.loads(e.source_refs)) == ["wiki:1", "wiki:2"], "实体引用应合并"
            assert sess.query(KgRelation).filter(KgRelation.repo_id == rid, KgRelation.source_ref == "wiki:1").count() == 1

        # wiki:1 重建后只剩 支付服务 → 幂等性（仅属 wiki:1）被选择性删除，关系重建
        _apply_doc(rid, "wiki:1", "h1b", {"title": "t"}, {
            "entities": [{"name": "支付服务", "type": "module", "description": "支付域"}],
            "relations": [],
        })
        with get_session() as sess:
            left = {e.name for e in sess.query(KgEntity).filter(KgEntity.repo_id == rid).all()}
            assert left == {"支付服务"}, "只属重建文档且消失的实体应删除"
            assert sess.query(KgRelation).filter(KgRelation.repo_id == rid).count() == 0, "wiki:1 旧关系应清空"
    finally:
        with get_session() as sess:
            sess.query(KgEntity).filter(KgEntity.repo_id == rid).delete(synchronize_session=False)
            sess.query(KgRelation).filter(KgRelation.repo_id == rid).delete(synchronize_session=False)
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
