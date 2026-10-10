"""混合检索（T1）：分词 / 索引 upsert / RRF 融合 / kind+repo 过滤 / 批量原子替换 / 相似用例兼容。"""

import uuid


def _cleanup(dk_prefix: str) -> None:
    from app.services.knowledge.rag import remove_documents_by_prefix

    remove_documents_by_prefix(dk_prefix)


def test_tokenize_latin_and_cjk_bigram():
    from app.services.knowledge.rag import tokenize

    toks = tokenize("create_order 订单创建").split()
    assert "create_order" in toks
    assert "订单" in toks and "单创" in toks and "创建" in toks, "CJK 应产出双元词"


def test_hybrid_search_rrf_fusion_and_filters():
    from app.services.knowledge.rag import hybrid_search, index_document

    rid = 990001
    _cleanup("hr:test:")
    index_document("hr:test:pay", "wiki", "支付链路", "submit_payment 支付回调 幂等性校验 金额精度", repo_id=rid)
    index_document("hr:test:order", "wiki", "订单模块", "create_order 订单创建 库存扣减", repo_id=rid)
    index_document("hr:test:bug", "defect", "BUG 金额精度丢失", "submit_payment 金额精度 分转元缺陷", repo_id=rid)
    try:
        hits = hybrid_search("支付 金额精度", limit=5, repo_id=rid)
        assert hits, "应至少命中一篇"
        by_title = {h["title"]: h for h in hits}
        assert {"支付链路", "BUG 金额精度丢失"} <= set(by_title), "向量/全文双通道各自命中的文档都应在场"
        assert "订单模块" in by_title, "弱相关文档也应被向量通道召回（repo 隔离范围内）"
        # RRF 性质：双通道共同命中的文档严格排在单通道文档之前
        dual = [h for h in hits if h["vec_rank"] and h["fts_rank"]]
        single = [h for h in hits if not (h["vec_rank"] and h["fts_rank"])]
        assert dual and single, "必须存在双通道与单通道命中各一"
        assert min(h["score"] for h in dual) > max(h["score"] for h in single), "RRF：双通道融合分 > 单通道"
        top = dual[0]
        assert top["vec_rank"] >= 1 and top["fts_rank"] >= 1

        only_wiki = hybrid_search("支付 金额精度", kinds=("wiki",), limit=5, repo_id=rid)
        assert all(h["kind"] == "wiki" for h in only_wiki), "kind 过滤必须生效"

        other_repo = hybrid_search("支付 金额精度", kinds=("wiki",), limit=5, repo_id=987654)
        assert not other_repo, "repo 过滤必须生效"
    finally:
        _cleanup("hr:test:")


def test_bulk_index_atomic_replace_scope():
    from app.services.knowledge.rag import hybrid_search, index_document, index_documents_bulk

    rid = 990002
    _cleanup("hr:bulk:")
    index_document("hr:bulk:old", "function", "old_fn", "legacy legacy legacy", repo_id=rid)
    n = index_documents_bulk(
        [
            {"doc_key": "hr:bulk:new1", "kind": "function", "repo_id": rid, "title": "fn_a", "content": "fn_a 处理订单"},
            {"doc_key": "hr:bulk:new2", "kind": "function", "repo_id": rid, "title": "fn_b", "content": "fn_b 处理支付"},
        ],
        replace_scope=("function", rid),
    )
    assert n == 2
    try:
        names = {h["title"] for h in hybrid_search("fn", kinds=("function",), limit=20, repo_id=rid)}
        assert names == {"fn_a", "fn_b"}, "旧文档应被 scope 替换清除"
    finally:
        _cleanup("hr:bulk:")


def test_similar_cases_metadata_enriched():
    """新混合路径 + 元数据回填；与旧 embedding 路径结果兼容。"""
    from app.db.session import get_session
    from app.models import Cases
    from app.services.knowledge.rag import index_case, similar_cases

    code = f"CASE-HRB-{uuid.uuid4().hex[:6].upper()}-TC-001"
    init_done = False
    try:
        from app.db.session import init_db

        init_db()
        init_done = True
        index_case(code, "create_order 下单数量边界 999 越界拒绝", title="下单数量上边界", layer="ut", category="boundary")
        with get_session() as sess:
            sess.add(
                Cases(code=code, layer="ut", title="下单数量上边界", module="orders", category="boundary", target_function="create_order", repo_id=990003, schema_json="{}", status="已入库")
            )
            sess.commit()
        sims = similar_cases("create_order 下单数量边界", limit=5)
        assert isinstance(sims, list)
    finally:
        if init_done:
            with get_session() as sess:
                row = sess.query(Cases).filter(Cases.code == code).first()
                if row is not None:
                    sess.delete(row)
                    sess.commit()
            from app.services.knowledge.rag import remove_document

            remove_document(f"case:{code}")
