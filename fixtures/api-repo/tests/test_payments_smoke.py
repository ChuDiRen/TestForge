"""存量冒烟测试。"""

import pytest

from app.payments_api.service import PayError, submit_payment


def test_submit_ok():
    r = submit_payment({"id": "u-1", "role": "buyer", "active": True}, "ORD-1", 9.9)
    assert r["status"] == "PAID"


def test_submit_rejects_zero():
    with pytest.raises(PayError) as ei:
        submit_payment({"id": "u-1", "role": "buyer", "active": True}, "ORD-1", 0)
    assert ei.value.code == "AMOUNT_INVALID"
