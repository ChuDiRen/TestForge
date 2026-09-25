"""变更驱动回归：mark_stale 命中目标函数匹配的用例（含已 stale 的，自愈闭环）。"""

import uuid


def test_mark_stale_marks_matching_cases():
    from gateway.regression import mark_stale
    from services.shared.db import get_session, init_db
    from services.shared.models import Cases

    init_db()
    code = f"CASE-STL-{uuid.uuid4().hex[:6].upper()}-TC-001"
    with get_session() as sess:
        sess.add(
            Cases(
                code=code,
                layer="ut",
                title="t",
                module="m",
                category="normal",
                target_function="fn_a",
                repo_id=987654321,
                schema_json="{}",
            )
        )
        sess.commit()
    try:
        hit = mark_stale(987654321, ["fn_b", "fn_a"])
        assert code in hit
        # 已 stale 的用例再次命中变更时仍要入回归集合（上轮失败须随下次变更重验）
        hit2 = mark_stale(987654321, ["fn_a"])
        assert code in hit2
        with get_session() as sess:
            row = sess.query(Cases).filter(Cases.code == code).first()
            assert row is not None and row.stale is True
    finally:
        with get_session() as sess:
            row = sess.query(Cases).filter(Cases.code == code).first()
            if row is not None:
                sess.delete(row)
                sess.commit()
