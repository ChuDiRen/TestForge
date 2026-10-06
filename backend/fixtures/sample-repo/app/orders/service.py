"""下单服务：create_order —— M1 生成管线的被测函数。

业务规则（验收条件）：
- 权限：买家角色才能下单；被禁用用户拒绝；管理员代下单放行。
- 边界：quantity ∈ [1, 999]；unit_price ∈ (0, 100000]；订单总额上限 1000000。
- 异常：未知 SKU / 库存不足 / 支付被风控拒绝。
- 幂等：相同 idempotency_key 重复请求返回原订单，不重复扣款。
"""

from __future__ import annotations

from app.inventory import client as inventory_client
from app.payments import client as payment_client

MAX_QUANTITY = 999
MAX_PRICE = 100000.0
MAX_TOTAL = 1000000.0

ORDERS: dict[str, dict] = {}  # order_id -> order（演示内存存储）
IDEMPOTENCY: dict[str, str] = {}  # idempotency_key -> order_id

_order_seq = 0


class OrderError(Exception):
    """订单业务异常，code 字段供用例断言。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _next_order_id() -> str:
    global _order_seq
    _order_seq += 1
    return f"ORD-{_order_seq:06d}"


def create_order(
    user: dict,
    sku: str,
    quantity: int,
    unit_price: float | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """创建订单并支付，返回订单 dict。

    参数：
        user: {"id": str, "role": "buyer"|"admin", "active": bool}
        sku: 库存单元编码
        quantity: 购买数量（1~999）
        unit_price: 单价；缺省取库存目录价
        idempotency_key: 幂等键，重复请求返回原订单

    异常：
        OrderError: USER_INVALID / USER_DISABLED / ROLE_FORBIDDEN /
                    SKU_INVALID / QUANTITY_INVALID / QUANTITY_TOO_LARGE /
                    PRICE_INVALID / PRICE_TOO_LARGE / TOTAL_TOO_LARGE /
                    SKU_NOT_FOUND / INSUFFICIENT_STOCK / PAYMENT_DECLINED
    """
    # ---- 权限 ----
    if not isinstance(user, dict) or not user.get("id"):
        raise OrderError("USER_INVALID")
    if not user.get("active", False):
        raise OrderError("USER_DISABLED")
    if user.get("role") not in ("buyer", "admin"):
        raise OrderError("ROLE_FORBIDDEN")

    # ---- 幂等 ----
    if idempotency_key and idempotency_key in IDEMPOTENCY:
        return ORDERS[IDEMPOTENCY[idempotency_key]]

    # ---- 边界 ----
    if not isinstance(sku, str) or not sku.strip():
        raise OrderError("SKU_INVALID")
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
        raise OrderError("QUANTITY_INVALID")
    if quantity > MAX_QUANTITY:
        raise OrderError("QUANTITY_TOO_LARGE")
    if unit_price is None:
        unit_price = inventory_client.get_price(sku)
    if not isinstance(unit_price, (int, float)) or isinstance(unit_price, bool) or unit_price <= 0:
        raise OrderError("PRICE_INVALID")
    if unit_price > MAX_PRICE:
        raise OrderError("PRICE_TOO_LARGE")
    total = round(unit_price * quantity, 2)
    if total > MAX_TOTAL:
        raise OrderError("TOTAL_TOO_LARGE")

    # ---- 依赖 ----
    try:
        if inventory_client.get_stock(sku) < quantity:
            raise OrderError("INSUFFICIENT_STOCK")
    except inventory_client.InventoryError as exc:
        raise OrderError("SKU_NOT_FOUND") from exc
    if not inventory_client.reserve(sku, quantity):
        raise OrderError("INSUFFICIENT_STOCK")

    try:
        payment = payment_client.charge(user["id"], total, idempotency_key or _next_order_id())
    except payment_client.PaymentError as exc:
        inventory_client.release(sku, quantity)
        raise OrderError("PAYMENT_DECLINED") from exc

    order = {
        "order_id": _next_order_id(),
        "user_id": user["id"],
        "sku": sku,
        "quantity": quantity,
        "unit_price": unit_price,
        "total": total,
        "status": "CREATED",
        "payment_id": payment["payment_id"],
    }
    ORDERS[order["order_id"]] = order
    if idempotency_key:
        IDEMPOTENCY[idempotency_key] = order["order_id"]
    return order
