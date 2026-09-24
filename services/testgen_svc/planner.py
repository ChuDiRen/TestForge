"""阶段 A：用例清单规划。mock=确定性模板（create_order 精选 + 通用兜底），real=LLM。"""

from __future__ import annotations

import logging
from typing import Any

from services.shared.llm import LLMClient, mock_task
from services.testgen_svc.fninfo import FnInfo
from services.testgen_svc.schemas import CasePlan, Patch, PlannedCase

log = logging.getLogger("testgen.plan")

# create_order 演示依赖的默认打桩（happy/boundary 用例共用）
_ORDER_DEFAULT_PATCHES = [
    Patch(module="app.inventory.client", attr="get_stock", value=10),
    Patch(module="app.inventory.client", attr="get_price", value=19.9),
    Patch(module="app.inventory.client", attr="reserve", value=True),
    Patch(module="app.payments.client", attr="charge", value={"payment_id": "pay-ok", "status": "PAID"}),
]

_P = lambda **kw: Patch(**kw)  # noqa: E731

_BUYER = {"id": "u-1", "role": "buyer", "active": True}


def _curated_create_order() -> list[PlannedCase]:
    """create_order 精选清单：正常/边界/异常/权限/幂等，输入与断言一一对应实现分支。"""
    base = lambda **extra: dict(user=_BUYER, sku="SKU-001", quantity=2, **extra)  # noqa: E731
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


def plan_cases(llm: LLMClient, fn: FnInfo, target: str, layer: str, options: dict[str, Any]) -> CasePlan:
    """两阶段之阶段 A：产出用例清单（schema 校验）。"""
    if llm.is_mock:
        if fn.name == "create_order":
            cases = _curated_create_order()
            return CasePlan(target=target, module=fn.module, layer=layer, cases=cases)
        # 通用兜底：happy + 交给覆盖守卫补齐
        from services.testgen_svc.fninfo import build_kwargs

        happy = PlannedCase(
            id="TC-001",
            title=f"{fn.name} happy path 应正常返回",
            category="normal",
            input=build_kwargs(fn),
            covers="主流程",
            source="plan",
        )
        return CasePlan(target=target, module=fn.module, layer=layer, cases=[happy])

    # real 模式：LLM 产出清单 JSON
    prompt = (
        f"{mock_task('plan')}\n"
        f"为函数 {target} 设计单测用例清单。函数源码与文档：\n{fn.source[:4000]}\n"
        f"要求：覆盖 normal/boundary/exception/permission；每条含 input kwargs、expected_error 或 expected_fields、"
        f"patches（对依赖模块打桩：module/attr/kind/value/exc）。\n返回 JSON：{{\"target\":..., \"cases\":[...]}}"
    )
    return llm.chat_json(
        [{"role": "user", "content": prompt}],
        schema=CasePlan,
        mock=None,
    )
