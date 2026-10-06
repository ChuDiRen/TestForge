"""库存客户端（演示用内存实现，生产替换为 RPC）。"""

from __future__ import annotations

STOCK: dict[str, int] = {"SKU-001": 100, "SKU-002": 5, "SKU-003": 0}
PRICE: dict[str, float] = {"SKU-001": 19.9, "SKU-002": 129.0, "SKU-003": 5.5}


class InventoryError(Exception):
    pass


def get_stock(sku: str) -> int:
    """查询可用库存；未知 SKU 抛 InventoryError。"""
    if sku not in STOCK:
        raise InventoryError(f"unknown sku: {sku}")
    return STOCK[sku]


def get_price(sku: str) -> float:
    if sku not in PRICE:
        raise InventoryError(f"unknown sku: {sku}")
    return PRICE[sku]


def reserve(sku: str, quantity: int) -> bool:
    """预留库存：成功扣减并返回 True；库存不足返回 False。"""
    if sku not in STOCK:
        raise InventoryError(f"unknown sku: {sku}")
    if STOCK[sku] < quantity:
        return False
    STOCK[sku] -= quantity
    return True


def release(sku: str, quantity: int) -> None:
    if sku in STOCK:
        STOCK[sku] += quantity
