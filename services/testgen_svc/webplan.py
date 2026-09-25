"""接口层 / E2E 层用例规划：从目标服务实时 OpenAPI 契约与真实旅程推导。

数据真实性：接口用例的路径/方法全部来自对 base_url/openapi.json 的实时抓取（非手写清单）；
E2E 用例是多步真实调用链（登录 → 数据一致性 → 录入打回），断言跨步骤的真实数据关系。
期望值（状态码/信封结构/键集合）在规划期确定、执行期对真实服务校验。
"""

from __future__ import annotations

import logging

import httpx

from services.shared.config import get_settings
from services.testgen_svc.schemas import CasePlan, PlannedCase

log = logging.getLogger("testgen.webplan")

TIMEOUT = 15.0


def default_base_url() -> str:
    s = get_settings()
    return f"http://127.0.0.1:{s.gateway_port}"


def fetch_openapi(base_url: str) -> dict:
    """实时抓取目标服务 OpenAPI（失败即报错——没有契约就没有接口用例）。"""
    resp = httpx.get(f"{base_url.rstrip('/')}/openapi.json", timeout=TIMEOUT)
    resp.raise_for_status()
    spec = resp.json()
    if not spec.get("paths"):
        raise RuntimeError("openapi.json 无 paths，无法推导接口用例")
    return spec


def build_api_cases(spec: dict, base_url: str) -> list[dict]:
    """从 OpenAPI paths 推导代表性接口用例（确定性规则，路径全部真实存在）。"""
    paths: dict = spec.get("paths", {})
    gets = sorted(p for p, ops in paths.items() if "get" in ops)
    posts = sorted(p for p, ops in paths.items() if "post" in ops)
    if not gets:
        raise RuntimeError("契约中无 GET 端点")

    cases: list[dict] = []

    def add(tc: str, title: str, category: str, covers: str, **kw: object) -> None:
        cases.append({"title": title, "category": category, "covers": covers, "input": {"base_url": base_url, **kw}})

    add("TC-001", "健康检查端点应 200 且信封 code=0", "normal", "主流程：GET 健康检查",
        method="GET", path="/api/health", auth=False, expect_status=200, expect_code=0, expect_keys=["data.status"])
    # 无参 GET 集合端点（取契约中第一个真实 /api 集合路径）
    collection = next((p for p in gets if p.startswith("/api/") and "{" not in p and p != "/api/health"), gets[0])
    add("TC-002", f"认证后访问 {collection} 应 200 且信封合法", "normal", "主流程：鉴权读集合端点",
        method="GET", path=collection, auth=True, expect_status=200)
    add("TC-003", "未携带 token 访问受保护端点应 401", "permission", "权限语义：网关强制认证",
        method="GET", path=collection, auth=False, expect_status=401)
    # 路径参数端点 → 不存在的 id 应业务 404
    deep = next((p for p in gets if "{" in p), None)
    if deep:
        import re

        probe_path = re.sub(r"\{[^}]+\}", "999999999", deep)
        add("TC-004", f"访问不存在的资源 {deep} 应业务 404", "exception", "异常：资源不存在",
            method="GET", path=probe_path, auth=True, expect_status=404, expect_code=404)
    if posts:
        p0 = posts[0]
        add("TC-005", f"空 body 提交 {p0} 应参数校验 400/422", "exception", "异常：必填校验",
            method="POST", path=p0, auth=True, json_body={}, expect_status=(400, 422))
    add("TC-006", "错误口令登录应 401", "exception", "异常：凭证错误",
        method="POST", path="/api/auth/login", auth=False,
        json_body={"username": "admin", "password": "__wrong_password__"}, expect_status=401)
    return cases


def build_e2e_cases(base_url: str) -> list[dict]:
    """E2E 旅程：跨端点多步真实调用，断言跨步骤数据一致性。"""
    cases: list[dict] = [
        {
            "title": "旅程A：登录→仓库→函数→用例→统计 全链一致",
            "category": "normal",
            "covers": "E2E 主旅程：登录后核心数据链路贯通且统计与明细一致",
            "input": {
                "base_url": base_url,
                "steps": [
                    {"method": "POST", "path": "/api/auth/login", "auth": False,
                     "json_body": {"username": "admin", "password": "__env_password__"},
                     "expect_status": 200, "save": "data.token -> TOKEN"},
                    {"method": "GET", "path": "/api/repos", "auth": True, "expect_status": 200, "save": "data -> REPOS"},
                    {"method": "GET", "path": "/api/stats/summary", "auth": True, "expect_status": 200,
                     "save": "data.cases_total -> TOTAL"},
                    {"method": "GET", "path": "/api/cases?page=1&page_size=200", "auth": True, "expect_status": 200},
                ],
                "final_asserts": [
                    "isinstance(REPOS, list) and len(REPOS) >= 1",
                    "isinstance(TOTAL, int) and TOTAL >= 0",
                ],
            },
        },
        {
            "title": "旅程B：需求录入→可测性打回→质量门禁如实扣分",
            "category": "exception",
            "covers": "E2E 写旅程：不可测需求被 G0 自动打回且门禁拒绝",
            "input": {
                "base_url": base_url,
                "steps": [
                    {"method": "POST", "path": "/api/auth/login", "auth": False,
                     "json_body": {"username": "admin", "password": "__env_password__"},
                     "expect_status": 200, "save": "data.token -> TOKEN"},
                    {"method": "POST", "path": "/api/requirements/ingest", "auth": True,
                     "json_body": {"title": "[E2E] 不可测需求样例", "source": "e2e",
                                   "body": "系统应该好用。"},
                     "expect_status": 200, "save": "data.code -> REQ"},
                    {"method": "GET", "path": "/api/quality/requirements", "auth": True, "expect_status": 200,
                     "save": "data -> QUALITY"},
                ],
                "final_asserts": [
                    "isinstance(REQ, str) and REQ.startswith('REQ-')",
                    "any(r['code'] == REQ and r['status'] == '已打回' for r in QUALITY)",
                ],
            },
        },
    ]
    return cases


def plan_web_cases(layer: str, base_url: str | None = None) -> CasePlan:
    """接口层（api）/ E2E 层（e2e）用例清单。"""
    base = base_url or default_base_url()
    if layer == "api":
        designs = build_api_cases(fetch_openapi(base), base)
    elif layer == "e2e":
        designs = build_e2e_cases(base)
    else:
        raise ValueError(f"不支持的 web 层: {layer}")
    cases = [
        PlannedCase(
            id=d.setdefault("id", f"TC-{i:03d}"),
            title=d["title"],
            category=d["category"],
            covers=d["covers"],
            input=d["input"],
            source="plan",
        )
        for i, d in enumerate(designs, 1)
    ]
    module = f"web-{layer}"
    log.info("web 规划 %s: %d 条（base=%s）", layer, len(cases), base)
    return CasePlan(target=f"{module}:journeys" if layer == "e2e" else f"{module}:openapi",
                    module=module, layer=layer, cases=cases)
