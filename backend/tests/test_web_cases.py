"""接口层/E2E 层规划与渲染单测：契约推导确定性 + 产物可编译可执行。"""


def _sample_spec() -> dict:
    return {
        "paths": {
            "/api/health": {"get": {}},
            "/api/repos": {"get": {}},
            "/api/repos/{repo_id}": {"get": {}},
            "/api/contracts": {"post": {}},
        }
    }


def test_build_api_cases_from_spec():
    from app.services.testgen.webplan import build_api_cases

    cases = build_api_cases(_sample_spec(), "http://127.0.0.1:8000")
    assert len(cases) >= 5
    inputs = [c["input"] for c in cases]
    # 健康检查 + 鉴权读 + 401 + 404 + 校验失败 + 错误口令
    assert inputs[0]["path"] == "/api/health" and inputs[0]["expect_code"] == 0
    assert any(i.get("auth") is False and i["expect_status"] == 401 for i in inputs)
    assert any("999999999" in i["path"] and i["expect_code"] == 404 for i in inputs)
    assert all(i["path"].startswith("/api/") for i in inputs)


def test_build_api_cases_requires_get_endpoints():
    from app.services.testgen.webplan import build_api_cases

    try:
        build_api_cases({"paths": {}}, "http://x")
        raise AssertionError("空契约应报错")
    except RuntimeError:
        pass


def test_render_web_file_compiles_and_runs():
    from app.schemas.testgen import CasePlan, PlannedCase
    from app.services.testgen.webcodegen import render_web_file
    from app.services.testgen.webplan import build_api_cases, build_e2e_cases

    designs = build_api_cases(_sample_spec(), "http://127.0.0.1:8000")
    cases = [PlannedCase(id=f"TC-{i:03d}", **d) for i, d in enumerate(designs, 1)]
    plan = CasePlan(target="web-api:openapi", module="web-api", layer="api", cases=cases)
    src = render_web_file(plan, "GEN-TEST")
    compile(src, "api.py", "exec")
    assert "httpx.request" in src and "BASE = " in src and "_token" in src
    assert "status_code == 200" in src and "status_code in (400, 422)" in src

    e_designs = build_e2e_cases("http://127.0.0.1:8000")
    e_cases = [PlannedCase(id=f"TC-{i:03d}", **d) for i, d in enumerate(e_designs, 1)]
    e_plan = CasePlan(target="web-e2e:journeys", module="web-e2e", layer="e2e", cases=e_cases)
    e_src = render_web_file(e_plan, "GEN-TEST")
    compile(e_src, "e2e.py", "exec")
    assert "TOKEN = _dig" in e_src and 'f"{{BASE}}' not in e_src  # 占位符必须已被解析为真实 f-string


def test_render_base_url_embedded():
    from app.schemas.testgen import CasePlan, PlannedCase
    from app.services.testgen.webcodegen import render_web_file
    from app.services.testgen.webplan import build_e2e_cases

    designs = build_e2e_cases("http://127.0.0.1:9999")
    cases = [PlannedCase(id=f"TC-{i:03d}", **d) for i, d in enumerate(designs, 1)]
    plan = CasePlan(target="web-e2e:journeys", module="web-e2e", layer="e2e", cases=cases)
    src = render_web_file(plan, "GEN-TEST")
    assert 'os.environ.get("TF_API_BASE_URL", "http://127.0.0.1:9999")' in src
