"""两阶段生成的结构化 schema（阶段 A 输出经 pydantic 严格校验）。

LLM 返回的 null / 错误形状（str 字段收到 list/dict）自动矫正为字段默认值——
真实模型常见噪声不应让整份清单作废；无法矫正的缺失字段仍显式报错。
"""

import json
from typing import Any

from pydantic import BaseModel, Field, model_validator
from pydantic_core import PydanticUndefined


def _coerce_model_fields(cls: type[BaseModel], data: Any, none_skip: tuple[str, ...] = ()) -> Any:
    """LLM 输出形状矫正：str 字段收到 list/dict → 归一为字符串；null/缺失 → 字段默认值。"""
    if isinstance(data, dict):
        for k, f in cls.model_fields.items():
            v = data.get(k)
            if f.annotation is str:
                if v is None or (k not in data and k not in none_skip):
                    data[k] = "" if f.get_default(call_default_factory=True) is PydanticUndefined else f.get_default(call_default_factory=True)
                elif isinstance(v, (list, tuple)):
                    data[k] = "; ".join(str(x) for x in v)
                elif isinstance(v, dict):
                    data[k] = json.dumps(v, ensure_ascii=False)
            elif v is None and k not in none_skip and k in data:
                data[k] = f.get_default(call_default_factory=True)
    return data


class Patch(BaseModel):
    """生成代码中的依赖打桩。"""

    module: str  # 如 app.inventory.client
    attr: str  # 如 get_stock
    kind: str = "value"  # value | raise
    value: Any = None  # kind=value 时的返回值（python 字面量）
    exc: str = ""  # kind=raise 时的异常类名
    exc_module: str = ""  # 异常类所在模块

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        return _coerce_model_fields(cls, data, none_skip=("value",))


class PlannedCase(BaseModel):
    id: str  # TC-001
    title: str
    category: str = "normal"  # normal|boundary|exception|permission|contract
    input: dict[str, Any] = Field(default_factory=dict)  # 目标函数 kwargs
    patches: list[Patch] = Field(default_factory=list)
    expected_error: str = ""  # 预期业务异常 code（如 QUANTITY_INVALID）
    expected_error_type: str = ""  # 预期异常类名（如 TypeError）；空=用模块约定错误类
    expected_fields: dict[str, Any] = Field(default_factory=dict)  # 返回值字段断言
    expected_return: Any = None  # 整体返回值断言（配合 assert_return）
    assert_return: bool = False  # True 时断言 result == expected_return
    expected_contains: list[str] = Field(default_factory=list)  # 结果文本须包含
    expected_not_contains: list[str] = Field(default_factory=list)  # 结果文本不得包含（脱敏/泄露类）
    covers: str = ""  # 覆盖分支说明
    source: str = ""  # 来源（plan|guard|repair）
    guard_added: bool = False

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        return _coerce_model_fields(cls, data, none_skip=("expected_return",))


class CasePlan(BaseModel):
    target: str  # 被测函数全名
    module: str = ""
    layer: str = "ut"
    cases: list[PlannedCase]

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        return _coerce_model_fields(cls, data)

    @property
    def categories(self) -> set[str]:
        return {c.category for c in self.cases}


CATEGORIES = ("normal", "boundary", "exception", "permission")
# 覆盖守卫检查表：每参数五类（PROMPT §8.2：NULL/空/极值/类型错/越权，按类归并为四 category）
GUARD_CHECKLIST = ("null", "empty", "extreme", "type-error", "privilege")
