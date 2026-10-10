"""API 接口测试（httpx TestClient）+ 失败分诊（CreateFromRun）单测。

接口自 M5+ 起全部走认证（admin 引导账号），测试用例先登录再请求。
"""

import json

ADMIN = {"username": "admin", "password": "testforge-admin"}  # settings.admin_password 默认值



def _client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def _auth(client) -> dict:
    r = client.post("/api/auth/login", json=ADMIN)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def test_health_envelope_shape():
    with _client() as client:
        r = client.get("/api/health")  # 健康检查豁免认证
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == 0 and body["message"] == "ok"
        assert body["data"]["status"] == "ok"
        assert "version" in body["data"]


def test_auth_required_and_login():
    with _client() as client:
        assert client.get("/api/cases").status_code == 401
        assert client.post("/api/auth/login", json={"username": "admin", "password": "bad"}).status_code == 401
        headers = _auth(client)
        assert client.get("/api/cases", headers=headers).status_code == 200


def test_trace_middleware_headers():
    with _client() as client:
        r = client.post("/api/repos/create", json={"url": ""}, headers=_auth(client))  # 校验失败路径也带 trace 头
        assert r.headers.get("X-Trace-Id", "").startswith("tr_")


def test_repo_validation_error_1001():
    with _client() as client:
        r = client.post("/api/repos/create", json={"url": ""}, headers=_auth(client))
        assert r.status_code == 400
        assert r.json()["code"] == 1001


def test_cases_endpoint_envelope_and_fields():
    with _client() as client:
        r = client.get("/api/cases", headers=_auth(client))
        assert r.status_code == 200
        data = r.json()["data"]
        assert {"total", "page", "page_size", "by_layer", "items"} <= set(data)
        for key in ("ut", "api", "fn", "e2e", "contract"):
            assert key in data["by_layer"]
        if data["items"]:
            item = data["items"][0]
            assert {"code", "layer", "title", "category", "status", "trace_id", "stale"} <= set(item)


def test_runs_and_quality_endpoints():
    with _client() as client:
        headers = _auth(client)
        r1 = client.get("/api/runs", headers=headers)
        assert r1.status_code == 200 and r1.json()["code"] == 0
        r2 = client.get("/api/quality/requirements", headers=headers)
        assert r2.status_code == 200
        for row in r2.json()["data"]:
            assert set(row["gates"]) == {"G0 可测性", "G1 知识就绪", "G2 用例覆盖", "G3 执行验证", "G4 缺陷清零", "G5 准出报告"}
            assert 0 <= row["quality_score"] <= 100


def test_unknown_case_review_404():
    with _client() as client:
        r = client.post("/api/cases/99999999/review", json={"action": "approve"}, headers=_auth(client))
        assert r.status_code == 404 and r.json()["code"] == 404


def test_requirement_ingest_requires_fields():
    with _client() as client:
        r = client.post("/api/requirements/ingest", json={"title": "", "body": ""}, headers=_auth(client))
        assert r.status_code == 400 and r.json()["code"] == 1001


def test_jobs_endpoint_empty_ok():
    with _client() as client:
        r = client.get("/api/jobs", headers=_auth(client))
        assert r.status_code == 200 and r.json()["code"] == 0
        assert isinstance(r.json()["data"], list)


def test_generation_export_404_for_unknown():
    with _client() as client:
        r = client.get("/api/generations/GEN-NOPE/export", headers=_auth(client))
        assert r.status_code == 404


# ---------------- 失败分诊：CreateFromRun（PROMPT §11 分诊覆盖） ----------------


def test_create_from_run_triage_and_assignee():
    from app.services.trace.defect_svc import create_from_run

    res = create_from_run(
        run_id="RUN-TEST-TRIAGE",
        case_codes=["CASE-NOT-EXIST-1", "CASE-NOT-EXIST-2"],
        req_code="REQ-TEST",
        trace_id="tr_triage_test",
        reason="单元测试：超修复轮次",
    )
    assert res["code"].startswith("BUG-")
    assert res["severity"] in ("致命", "严重", "一般")
    assert res["assignee"]  # 自动指派（含“待指派”兜底）
    assert res["status"] == "新建"

    from app.db.session import get_session
    from app.models import Defects

    with get_session() as sess:
        d = sess.query(Defects).filter(Defects.code == res["code"]).first()
        assert d is not None
        assert json.loads(d.case_codes) == ["CASE-NOT-EXIST-1", "CASE-NOT-EXIST-2"]
        assert d.origin_run == "RUN-TEST-TRIAGE"
        assert d.req_code == "REQ-TEST"
        assert d.trace_id == "tr_triage_test"
        d.status = "已关闭"  # 清理测试数据，避免污染台账
        sess.commit()


def test_triage_severity_escalates_with_failure_count():
    from app.services.trace.defect_svc import create_from_run

    many = create_from_run("RUN-TRIAGE-BIG", [f"C{i}" for i in range(8)], "REQ-T2", "tr_t2", "批量失败")
    few = create_from_run("RUN-TRIAGE-SMALL", ["C1"], "REQ-T2", "tr_t2", "单条失败")
    assert many["severity"] == "致命" and few["severity"] == "严重"

    from app.db.session import get_session
    from app.models import Defects

    with get_session() as sess:
        for code in (many["code"], few["code"]):
            d = sess.query(Defects).filter(Defects.code == code).first()
            d.status = "已关闭"
        sess.commit()
