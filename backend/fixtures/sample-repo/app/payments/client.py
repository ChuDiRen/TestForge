"""支付客户端（演示用内存实现，生产替换为支付网关）。"""

from __future__ import annotations


class PaymentError(Exception):
    pass


# 演示规则：金额带 .01 尾数 或 超过 99999 触发风控拒绝
RISK_AMOUNT = 99999.0


def charge(user_id: str, amount: float, idempotency_key: str) -> dict:
    """扣款。风控拒绝抛 PaymentError；成功返回支付凭据。"""
    if amount <= 0:
        raise PaymentError("INVALID_AMOUNT")
    if amount > RISK_AMOUNT or abs(amount * 100 - int(amount * 100) - 1) < 1e-9:
        raise PaymentError("RISK_DECLINED")
    return {"payment_id": f"pay-{idempotency_key}", "user_id": user_id, "amount": amount, "status": "PAID"}
