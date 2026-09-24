# sample-repo（M1 验收被测仓库）

模拟电商下单服务：`app/orders/service.py::create_order`。

- 依赖：`app/inventory/client.py`（库存）、`app/payments/client.py`（支付）
- 存量测试：`tests/test_orders_smoke.py`
- 设计用途：tree-sitter 索引 + AI 用例生成 + 沙箱执行的演示标的，
  分支覆盖：权限（禁用用户/角色）、边界（数量/价格极值）、异常（SKU 不存在/库存不足/支付失败）、幂等（重复 key）。
