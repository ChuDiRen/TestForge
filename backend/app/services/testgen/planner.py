"""阶段 A：用例清单规划。精选/探针靶标确定性策略，其余函数 DeepSeek 规划。"""

from __future__ import annotations

import logging
from typing import Any

from app.schemas.testgen import CasePlan, Patch, PlannedCase
from app.services.testgen.fninfo import FnInfo

log = logging.getLogger("testgen.plan")

# create_order 演示依赖的默认打桩（happy/boundary 用例共用；库存 1000 覆盖上界 999）
_ORDER_DEFAULT_PATCHES = [
    Patch(module="app.inventory.client", attr="get_stock", value=1000),
    Patch(module="app.inventory.client", attr="get_price", value=19.9),
    Patch(module="app.inventory.client", attr="reserve", value=True),
    Patch(module="app.payments.client", attr="charge", value={"payment_id": "pay-ok", "status": "PAID"}),
]

_P = lambda **kw: Patch(**kw)  # noqa: E731

_BUYER = {"id": "u-1", "role": "buyer", "active": True}

# 探针式生成靶标（本仓库真实函数）：这里只设计【输入】，期望值由 probe 对真实代码
# 执行捕获（特征化）。exception 类输入同时预压覆盖守卫的 NULL/空/类型错补齐规则。
_R = "作为买家，我希望下单数量不超过 999。验收条件：数量 1~999 允许下单。"
_REST_SPEC_V1 = '{"paths": {"/api/orders": {"post": {"__fields__": {"id": {"required": true}}}}, "/api/orders/{id}": {"get": {"__fields__": {}}}}, "error_codes": ["ORDER_NOT_FOUND"]}'
_REST_SPEC_V2_DELETED = '{"paths": {"/api/orders": {"post": {"__fields__": {"id": {"required": true}}}}}, "error_codes": ["ORDER_NOT_FOUND"]}'
_GRPC_V1 = '{"rpcs": ["Generate", "Execute"], "fields": {"case": {"code": "string"}}}'
_GRPC_V2_RPC_DELETED = '{"rpcs": ["Generate"], "fields": {"case": {"code": "string"}}}'
_TOPIC_V1 = '{"fields": {"case_code": {"type": "string"}, "status": {"type": "string"}}}'
_TOPIC_V2_TYPE_CHANGED = '{"fields": {"case_code": {"type": "string"}, "status": {"type": "int"}}}'

PROBE_INPUT_DESIGNS: dict[str, list[dict]] = {
    # services/shared/rag.py：确定性词袋 hash 向量
    "embed": [
        {"title": "中文混合数字文本的向量确定性", "category": "normal", "input": {"text": "订单 数量 上限 999"}, "covers": "主流程：中文+数字 token 哈希"},
        {"title": "英文文本的向量确定性", "category": "normal", "input": {"text": "hello world sanitize"}, "covers": "主流程：英文小写化 token"},
        {"title": "超长文本向量维度恒定", "category": "boundary", "input": {"text": "密度" * 5000}, "covers": "边界：10000 字输入维度不变"},
        {"title": "空文本应返回零向量", "category": "exception", "input": {"text": ""}, "covers": "异常：空输入（预压守卫空串）"},
        {"title": "错误类型输入应抛 AttributeError", "category": "exception", "input": {"text": ["not-str"]}, "covers": "异常：list 无 lower（预压守卫类型错）"},
    ],
    # app/services/req/parser.py：需求规则抽取（纯 regex）
    "extract_rules": [
        {"title": "完整用户故事应抽取故事/边界/验收三类规则", "category": "normal", "input": {"text": _R}, "covers": "主流程：三类规则一次抽全"},
        {"title": "权限关键词应产生权限规则", "category": "permission", "input": {"text": "管理员账号禁止越权操作"}, "covers": "权限语义：关键词命中"},
        {"title": "空文本应返回待确认规则", "category": "exception", "input": {"text": ""}, "covers": "异常：空输入返回待确认（预压守卫空串）"},
        {"title": "错误类型输入应抛 TypeError", "category": "exception", "input": {"text": ["非法输入"]}, "covers": "异常：list 进 regex（预压守卫类型错）"},
    ],
    # services/contract_registry/service.py：契约 diff/breaking 判定（真实契约结构 JSON）
    "diff_specs": [
        {"title": "结构无变化应判定非 breaking", "category": "normal", "input": {"old_spec": _REST_SPEC_V1, "new_spec": _REST_SPEC_V1, "ctype": "rest"}, "covers": "主流程：同 spec diff"},
        {"title": "REST 端点删除应判定 breaking", "category": "normal", "input": {"old_spec": _REST_SPEC_V1, "new_spec": _REST_SPEC_V2_DELETED, "ctype": "rest"}, "covers": "breaking：端点删除"},
        {"title": "gRPC rpc 删除应判定 breaking", "category": "normal", "input": {"old_spec": _GRPC_V1, "new_spec": _GRPC_V2_RPC_DELETED, "ctype": "grpc"}, "covers": "breaking：rpc 删除"},
        {"title": "topic 字段类型变更应判定 breaking", "category": "normal", "input": {"old_spec": _TOPIC_V1, "new_spec": _TOPIC_V2_TYPE_CHANGED, "ctype": "topic"}, "covers": "breaking：事件字段类型"},
        {"title": "旧 spec 为空应只报新增非 breaking", "category": "exception", "input": {"old_spec": "", "new_spec": _REST_SPEC_V1, "ctype": "rest"}, "covers": "异常：旧契约为空（预压守卫空串）"},
        {"title": "新 spec 为空应判定全部端点删除 breaking", "category": "exception", "input": {"old_spec": _REST_SPEC_V1, "new_spec": "", "ctype": "rest"}, "covers": "异常：新契约丢失"},
        {"title": "未知契约类型应跳过 diff", "category": "exception", "input": {"old_spec": _REST_SPEC_V1, "new_spec": _REST_SPEC_V1, "ctype": ""}, "covers": "异常：ctype 未匹配（预压守卫空串）"},
        {"title": "三个参数全为 list 应安全返回无结构变更", "category": "exception", "input": {"old_spec": [], "new_spec": [], "ctype": []}, "covers": "异常：falsy 输入走 {} 兜底（预压守卫类型错）"},
    ],
}


def _curated_create_order() -> list[PlannedCase]:
    """create_order 精选清单：正常/边界/异常/权限/幂等，输入与断言一一对应实现分支。"""
    base = lambda **extra: dict({"user": _BUYER, "sku": "SKU-001", "quantity": 2}, **extra)  # noqa: E731
    cases: list[PlannedCase] = [
        PlannedCase(id="TC-001", title="正常下单应成功并返回订单", category="normal", input=base(), patches=list(_ORDER_DEFAULT_PATCHES), expected_fields={"status": "CREATED", "total": 39.8, "user_id": "u-1"}, covers="主流程：校验→库存→支付→落单", source="plan"),
        PlannedCase(id="TC-002", title="数量下界 1 应成功", category="boundary", input=base(quantity=1), patches=list(_ORDER_DEFAULT_PATCHES), expected_fields={"total": 19.9}, covers="边界：quantity=1", source="plan"),
        PlannedCase(id="TC-003", title="数量上界 999 应成功", category="boundary", input=base(quantity=999), patches=list(_ORDER_DEFAULT_PATCHES), expected_fields={"total": 19880.1}, covers="边界：quantity=999", source="plan"),
        PlannedCase(id="TC-004", title="数量 1000 超上界应拒绝", category="boundary", input=base(quantity=1000), patches=list(_ORDER_DEFAULT_PATCHES), expected_error="QUANTITY_TOO_LARGE", covers="边界：quantity>999", source="plan"),
        PlannedCase(id="TC-005", title="单价超上限应拒绝", category="boundary", input=base(unit_price=100000.01), patches=list(_ORDER_DEFAULT_PATCHES), expected_error="PRICE_TOO_LARGE", covers="边界：unit_price>100000", source="plan"),
        PlannedCase(id="TC-006", title="总额超上限应拒绝", category="boundary", input=base(quantity=999, unit_price=1002), patches=list(_ORDER_DEFAULT_PATCHES), expected_error="TOTAL_TOO_LARGE", covers="边界：total>1000000", source="plan"),
        PlannedCase(id="TC-007", title="数量 0 应拒绝", category="exception", input=base(quantity=0), patches=list(_ORDER_DEFAULT_PATCHES), expected_error="QUANTITY_INVALID", covers="异常：quantity<1", source="plan"),
        PlannedCase(
            id="TC-008",
            title="未知 SKU 应拒绝",
            category="exception",
            input=base(sku="SKU-NOPE"),
            patches=[_P(module="app.inventory.client", attr="get_price", value=19.9), _P(module="app.inventory.client", attr="get_stock", kind="raise", exc="InventoryError", exc_module="app.inventory.client", value=None), _P(module="app.inventory.client", attr="reserve", value=True), _P(module="app.payments.client", attr="charge", value={"payment_id": "pay-ok", "status": "PAID"})],
            expected_error="SKU_NOT_FOUND",
            covers="异常：SKU 不存在（InventoryError 归一化）",
            source="plan",
        ),
        PlannedCase(id="TC-009", title="库存不足应拒绝", category="exception", input=base(quantity=5), patches=[_P(module="app.inventory.client", attr="get_stock", value=1), _P(module="app.inventory.client", attr="get_price", value=19.9), _P(module="app.inventory.client", attr="reserve", value=True), _P(module="app.payments.client", attr="charge", value={"payment_id": "pay-ok", "status": "PAID"})], expected_error="INSUFFICIENT_STOCK", covers="异常：库存不足短路", source="plan"),
        PlannedCase(
            id="TC-010",
            title="支付风控拒绝应回滚库存并拒绝",
            category="exception",
            input=base(),
            patches=[_P(module="app.inventory.client", attr="get_stock", value=10), _P(module="app.inventory.client", attr="get_price", value=19.9), _P(module="app.inventory.client", attr="reserve", value=True), _P(module="app.payments.client", attr="charge", kind="raise", exc="PaymentError", exc_module="app.payments.client", value=None)],
            expected_error="PAYMENT_DECLINED",
            covers="异常：支付失败→释放库存",
            source="plan",
        ),
        PlannedCase(id="TC-011", title="禁用用户应拒绝", category="permission", input=base(user={"id": "u-2", "role": "buyer", "active": False}), patches=list(_ORDER_DEFAULT_PATCHES), expected_error="USER_DISABLED", covers="权限：用户禁用", source="plan"),
        PlannedCase(id="TC-012", title="非授权角色应拒绝", category="permission", input=base(user={"id": "u-3", "role": "guest", "active": True}), patches=list(_ORDER_DEFAULT_PATCHES), expected_error="ROLE_FORBIDDEN", covers="权限：角色越权", source="plan"),
        PlannedCase(id="TC-013", title="相同幂等键重复请求应返回原订单", category="boundary", input=base(idempotency_key="idem-1"), patches=list(_ORDER_DEFAULT_PATCHES), expected_fields={"status": "CREATED"}, covers="幂等：重复 idempotency_key 不重复下单", source="plan"),
    ]
    return cases


def _curated_sanitize_text() -> list[PlannedCase]:
    """sanitize_text（services/shared/sanitize.py，本仓库真实函数）精选清单。

    期望值全部来自该函数真实行为（非模拟）；空串/类型错两条 exception 预压覆盖守卫的补齐规则。
    """
    cases: list[PlannedCase] = [
        PlannedCase(id="TC-001", title="无敏感信息的文本应原样保留", category="normal", input={"text": "user login ok"}, expected_return="user login ok", assert_return=True, covers="主流程：未命中规则原样返回", source="plan"),
        PlannedCase(id="TC-002", title="Bearer 令牌应掩码", category="normal", input={"text": "Bearer abc.DEF-123 后续"}, expected_not_contains=["abc.DEF-123"], expected_contains=["Bearer ***"], covers="主流程：Bearer 令牌掩码", source="plan"),
        PlannedCase(id="TC-003", title="password 键值应掩码", category="normal", input={"text": "password=hunter2"}, expected_not_contains=["hunter2"], expected_contains=["password=***"], covers="主流程：password 键值掩码", source="plan"),
        PlannedCase(id="TC-004", title="sk- 前缀密钥应掩码", category="normal", input={"text": "sk-prod12345abcdefgh"}, expected_return="sk-***", assert_return=True, covers="主流程：sk- 密钥掩码", source="plan"),
        PlannedCase(id="TC-005", title="api_key 键值应掩码且保留后续参数", category="normal", input={"text": "api_key=abc123&x=1"}, expected_return="api_key=***", assert_return=True, covers="主流程：api_key 键值掩码", source="plan"),
        PlannedCase(id="TC-006", title="大小写 Authorization 头组合应全掩码", category="boundary", input={"text": "Authorization: Bearer eyJhbGci.OKen.me_1 x=2"}, expected_return="Authorization=*** *** x=2", assert_return=True, covers="边界：头字段与 Bearer 双规则叠加", source="plan"),
        PlannedCase(id="TC-007", title="空字符串输入应返回空字符串", category="exception", input={"text": ""}, expected_return="", assert_return=True, covers="异常：空输入安全返回（守卫空串预压）", source="plan"),
        PlannedCase(id="TC-008", title="None 输入应安全降级为空字符串", category="exception", input={"text": None}, expected_return="", assert_return=True, covers="异常：None 容错", source="plan"),
        PlannedCase(id="TC-009", title="错误类型输入应抛 TypeError", category="exception", input={"text": ["not-a-string"]}, expected_error_type="TypeError", covers="异常：list 输入 re.sub 抛 TypeError（守卫类型错预压）", source="plan"),
        PlannedCase(id="TC-010", title="authorization 头中的令牌不可回读", category="permission", input={"text": "authorization: Bearer tok-9a8b7c6d5e"}, expected_return="authorization=*** ***", assert_return=True, covers="权限语义：凭证掩码后不可复原", source="plan"),
    ]
    return cases


def plan_cases(fn: FnInfo, target: str, layer: str, options: dict[str, Any]) -> CasePlan:
    """两阶段之阶段 A：产出用例清单（schema 校验）。

    精选/探针靶标走确定性策略（期望值来自真实行为/真实执行捕获）；
    其余函数由 DeepSeek 规划（真实 LLM，schema 校验）。
    """
    if fn.name == "create_order":
        cases = _curated_create_order()
    elif fn.name == "sanitize_text":
        cases = _curated_sanitize_text()
    elif fn.name in PROBE_INPUT_DESIGNS:
        # 探针式：只设计输入，期望值由 probe 对真实代码执行捕获
        cases = [
            PlannedCase(id=f"TC-{i:03d}", source="plan", **design)
            for i, design in enumerate(PROBE_INPUT_DESIGNS[fn.name], 1)
        ]
    else:
        # DeepSeek×deepagents 智能体：自主探索上下文并设计输入；期望值由探针捕获
        from app.services.testgen.agent import plan_with_deepagent

        cases = plan_with_deepagent(fn, target).cases
    return CasePlan(target=target, module=fn.module, layer=layer, cases=cases)
