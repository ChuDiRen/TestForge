"""被测函数元信息（从索引/源码提取签名与参数）。"""

import ast
import dataclasses
from typing import Any


@dataclasses.dataclass
class ParamInfo:
    name: str
    annotation: str = ""
    default: str = ""  # "" 表示无默认
    has_default: bool = False


@dataclasses.dataclass
class FnInfo:
    name: str
    module: str
    params: list[ParamInfo]
    docstring: str = ""
    source: str = ""


def parse_signature(source: str, name: str) -> FnInfo:
    """从函数源码解析参数表（ast，稳定且不导入目标代码）。"""
    params: list[ParamInfo] = []
    docstring = ""
    try:
        tree = ast.parse(source)
        fn = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn = node
                if not docstring:
                    docstring = ast.get_docstring(node) or ""
                if fn.name == name:
                    break
        if fn is not None:
            for p in fn.args.args:
                ann = ast.unparse(p.annotation) if p.annotation else ""
                params.append(ParamInfo(name=p.arg, annotation=ann))
            defaults = fn.args.defaults
            offset = len(fn.args.args) - len(defaults)
            for i, d in enumerate(defaults):
                params[offset + i].default = ast.unparse(d)
                params[offset + i].has_default = True
            name = fn.name
    except SyntaxError:
        pass
    return FnInfo(name=name, module="", params=params, docstring=docstring, source=source)


def build_kwargs(fn: FnInfo) -> dict[str, Any]:
    """按类型注解/默认值构造 happy-path kwargs。"""
    kwargs: dict[str, Any] = {}
    for p in fn.params:
        if p.has_default:
            continue
        kwargs[p.name] = _value_for(p)
    return kwargs


def _value_for(p: ParamInfo) -> Any:
    ann = p.annotation.lower()
    if "int" in ann:
        return 2
    if "float" in ann:
        return 1.5
    if "bool" in ann:
        return True
    if "dict" in ann:
        return {"id": "u-1", "role": "buyer", "active": True}
    if "list" in ann:
        return []
    return "SAMPLE"
