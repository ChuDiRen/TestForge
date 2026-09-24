"""支付提交服务：submit_payment —— 多仓/契约上下文演示被测函数。

业务规则：
- 权限：active 买家才能发起支付。
- 边界：金额 (0, 10000]，币种支持 CNY/USD。
- 异常：渠道不可用 → CHANNEL_DOWN；余额不足 → INSUFFICIENT_BALANCE。
"""

from __future__ import annotations

SUPPORTED_CCY = ("CNY", "USD")
MAX_AMOUNT = 10000.0


class PayError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def submit_payment(user: dict, order_id: str, amount: float, ccy: str = "CNY") -> dict:
    if not isinstance(user, dict) or not user.get("id"):
        raise PayError("USER_INVALID")
    if not user.get("active", False):
        raise PayError("USER_DISABLED")
    if user.get("role") != "buyer":
        raise PayError("ROLE_FORBIDDEN")
    if not order_id or not isinstance(order_id, str):
        raise PayError("ORDER_INVALID")
    if not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount <= 0:
        raise PayError("AMOUNT_INVALID")
    if amount > MAX_AMOUNT:
        raise PayError("AMOUNT_TOO_LARGE")
    if ccy not in SUPPORTED_CCY:
        raise PayError("CCY_UNSUPPORTED")
    return {"payment_id": f"pay-{order_id}", "status": "PAID", "amount": amount, "ccy": ccy}
