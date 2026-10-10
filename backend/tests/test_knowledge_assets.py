"""入口②知识资产统一上传：四类资产路由 / 敏感门卫 / 缺陷结构化导入 / 闭环总览。

不依赖 LLM：评估门卫在 LLM 不可用时自动放行（门卫不挡人）；req_doc 用例在
无 LLM Key 时 skip（需求解析管线是真实 LLM 调用，不做 mock）。
"""

import csv
import io
import uuid

import pytest

ADMIN = {"username": "admin", "password": "testforge-admin"}
ASSET_TEXT = (
    "## 支付模块业务规则\n\n"
    "1. 单笔支付金额上限 50,000 元，超过需走人工审核通道。\n"
    "2. 同一订单号 10 分钟内只允许提交一次支付请求（幂等键）。\n"
    "3. 支付超时 30 秒触发对账补偿任务，补偿最多重试 3 次。\n"
    "4. 退款金额不得超过实付金额，部分退款可多次发起。"
)


def _llm_key_present() -> bool:
    from services.shared.config import get_settings

    return bool(get_settings().llm_api_key)


def _make_client():
    from fastapi.testclient import TestClient

    from gateway.main import app

    return TestClient(app)


def _auth(client) -> dict:
    r = client.post("/api/auth/login", json=ADMIN)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def _first_repo_id() -> int:
    from services.shared.db import get_session
    from services.shared.models import Repos

    with get_session() as sess:
        row = sess.query(Repos.id).order_by(Repos.id.desc()).first()
        return int(row[0]) if row else 0


def test_tech_doc_upload_dual_write():
    """技术文档：user_doc 检索行 + kg 管线状态行双写（两套体系在此汇流）。"""
    with _make_client() as client:
        headers = _auth(client)
        rid = _first_repo_id()
        title = f"接口约定-资产上传测试-{uuid.uuid4().hex[:6]}"
        r = client.post(
            "/api/knowledge/assets/upload",
            files={"file": ("api-rules.txt", ASSET_TEXT.encode("utf-8"), "text/plain")},
            data={"kind": "tech_doc", "repo_id": str(rid), "title": title},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["kind"] == "tech_doc" and data["chars"] > 100
        assert data["doc_key"].startswith("userdoc:")
        try:
            from sqlalchemy import text

            from services.shared.db import get_session

            with get_session() as sess:
                row = sess.execute(
                    text("SELECT kind, meta FROM rag_documents WHERE doc_key = :k"), {"k": data["doc_key"]}
                ).first()
                assert row is not None and row[0] == "user_doc"
                assert "asset_kind" in str(row[1]), "meta 应带 asset-upload 来源"
                kg = sess.execute(
                    text("SELECT status FROM doc_status WHERE doc_key = :k"), {"k": data["kg_doc_key"]}
                ).first()
                assert kg is not None and kg[0] in ("pending", "processing", "ok"), "kg 管线应已受理"
        finally:
            from sqlalchemy import text

            from services.shared.db import get_session

            with get_session() as sess:
                for k in (data["doc_key"], data["kg_doc_key"]):
                    sess.execute(text("DELETE FROM rag_documents WHERE doc_key = :k"), {"k": k})
                    sess.execute(text("DELETE FROM doc_status WHERE doc_key = :k"), {"k": k})
                sess.commit()


def test_sensitive_document_rejected():
    """敏感门卫对解析后文本生效（pdf/docx 走同一入口同一守门）。"""
    with _make_client() as client:
        headers = _auth(client)
        evil = "数据库配置说明\n\nprod 连接串：postgres://admin:secret9@db.internal/app\n" + "补充说明" * 10
        r = client.post(
            "/api/knowledge/assets/upload",
            files={"file": ("leak.txt", evil.encode("utf-8"), "text/plain")},
            data={"kind": "tech_doc"},
            headers=headers,
        )
        assert r.status_code == 422
        assert "敏感" in r.json()["message"]


def test_defect_csv_import():
    """历史缺陷 csv：结构化入 defects 表 + defect kind 摘要索引（生成 bugs 路可召回）。"""
    rid = _first_repo_id()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["标题", "描述", "严重", "状态", "模块", "需求编号"])
    w.writerow(["支付回调重复扣款", "幂等键缺失导致回调重放时重复入账", "P1", "已修复", "payment", ""])
    w.writerow(["", "空标题行应被跳过", "P2", "", "", ""])
    w.writerow(["订单列表分页错误", "page size 越界返回 500", "严重", "新建", "order", ""])
    content = buf.getvalue().encode("utf-8-sig")
    with _make_client() as client:
        headers = _auth(client)
        r = client.post(
            "/api/knowledge/assets/upload",
            files={"file": ("defects.csv", content, "text/csv")},
            data={"kind": "defect", "repo_id": str(rid)},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        codes = data["defect_codes"]
        from services.shared.models import Defects
        from services.shared.rag import remove_document

        try:
            assert data["imported"] == 2, "空标题行跳过"
            assert len(codes) == 2 and all(c.startswith("BUG-") for c in codes)
            with __import__("services.shared.db", fromlist=["get_session"]).get_session() as sess:
                rows = sess.query(Defects).filter(Defects.code.in_(codes)).all()
                by_code = {d.code: (d.severity, d.status) for d in rows}
                assert by_code[codes[0]] == ("严重", "已修复"), "P1 → 严重；状态透传"
                assert by_code[codes[1]] == ("严重", "新建")
        finally:
            from services.shared.db import get_session

            with get_session() as sess:
                sess.query(Defects).filter(Defects.code.in_(codes)).delete(synchronize_session=False)
                sess.commit()
            for c in codes:
                remove_document(f"defect:{c}")


def test_assets_summary_shape():
    with _make_client() as client:
        headers = _auth(client)
        r = client.get("/api/knowledge/assets/summary", headers=headers)
        assert r.status_code == 200
        data = r.json()["data"]
        assert "kinds" in data and "defects_open" in data and "cases" in data
        assert isinstance(data["kinds"], dict)


@pytest.mark.skipif(not _llm_key_present(), reason="需求解析管线是真实 LLM 调用——无 LLM_API_KEY 时跳过")
def test_req_doc_upload_ingests_requirement():
    with _make_client() as client:
        headers = _auth(client)
        rid = _first_repo_id()
        r = client.post(
            "/api/knowledge/assets/upload",
            files={"file": ("req.txt", ASSET_TEXT.encode("utf-8"), "text/plain")},
            data={"kind": "req_doc", "repo_id": str(rid), "title": "支付幂等与退款规则（资产上传）"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["req_code"].startswith("REQ-")
        assert 0 <= float(data["testability"]) <= 100
