"""存量冒烟测试（接入前已有，演示“增量”场景）。"""

from app.inventory import client as inventory_client
from app.orders.service import OrderError, create_order
from app.payments import client as payment_client


def _buyer():
    return {"id": "u-1", "role": "buyer", "active": True}


def test_create_order_happy_path(monkeypatch):
    monkeypatch.setattr(inventory_client, "get_stock", lambda sku: 10)
    monkeypatch.setattr(inventory_client, "get_price", lambda sku: 19.9)
    monkeypatch.setattr(inventory_client, "reserve", lambda sku, qty: True)
    monkeypatch.setattr(payment_client, "charge", lambda uid, amt, key: {"payment_id": "pay-x", "status": "PAID"})

    order = create_order(_buyer(), "SKU-001", 2)
    assert order["status"] == "CREATED"
    assert order["total"] == 39.8


def test_create_order_rejects_zero_quantity():
    import pytest

    with pytest.raises(OrderError) as ei:
        create_order(_buyer(), "SKU-001", 0)
    assert ei.value.code == "QUANTITY_INVALID"
