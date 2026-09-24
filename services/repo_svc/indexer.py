"""tree-sitter Python 索引：函数卡片（签名/源码/位置）+ 调用边。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from tree_sitter import Parser
from tree_sitter_language_pack import get_parser

log = logging.getLogger("repo-svc.treesitter")

_SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".tox", "build", "dist", ".pytest_cache"}


@dataclass
class FnCard:
    module: str
    name: str
    signature: str
    source: str
    file: str
    line: int
    docstring: str = ""
    calls: list[str] = field(default_factory=list)


def _new_parser() -> Parser:
    return get_parser("python")


def _module_of(py_file: Path, root: Path) -> str:
    rel = py_file.relative_to(root).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _node_src(src: bytes, node) -> str:  # type: ignore[no-untyped-def]
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def parse_python_file(py_file: Path, root: Path) -> list[FnCard]:
    """解析单个 py 文件中的顶层函数（含装饰器签名与调用列表）。"""
    try:
        src = py_file.read_bytes()
        tree = _new_parser().parse(src)
    except Exception as exc:  # noqa: BLE001
        log.warning("parse failed %s: %s", py_file, exc)
        return []

    module = _module_of(py_file, root)
    cards: list[FnCard] = []

    def walk(node) -> None:  # type: ignore[no-untyped-def]
        for child in node.children:
            if child.type == "function_definition":
                cards.append(_card(child, src, module, py_file, root))
                # 不深入函数体（闭包不入库）
            elif child.type == "class_definition":
                cls = _node_src(src, child.child_by_field_name("name"))
                for sub in child.children:
                    if sub.type == "block":
                        for m in sub.children:
                            if m.type == "function_definition":
                                card = _card(m, src, module, py_file, root)
                                card.name = f"{cls}.{card.name}"
                                cards.append(card)
                walk(child)
            else:
                walk(child)

    walk(tree.root_node)
    return cards


def _card(node, src: bytes, module: str, py_file: Path, root: Path) -> FnCard:  # type: ignore[no-untyped-def]
    name_node = node.child_by_field_name("name")
    params_node = node.child_by_field_name("parameters")
    body = node.child_by_field_name("body")
    name = _node_src(src, name_node)
    signature = f"{name}{_node_src(src, params_node)}"
    docstring = ""
    if body is not None and len(body.children) > 0:
        first = body.children[0]
        if first.type == "expression_statement" and first.children and first.children[0].type == "string":
            docstring = _node_src(src, first.children[0]).strip()[:2000]
    calls: list[str] = []
    if body is not None:
        _collect_calls(body, src, calls)
    return FnCard(
        module=module,
        name=name,
        signature=signature,
        source=_node_src(src, node),
        file=str(py_file.relative_to(root)).replace("\\", "/"),
        line=node.start_point[0] + 1,
        docstring=docstring,
        calls=calls,
    )


def _collect_calls(node, src: bytes, out: list[str]) -> None:  # type: ignore[no-untyped-def]
    if node.type == "call":
        fn = node.child_by_field_name("function")
        if fn is not None:
            out.append(_node_src(src, fn).split(".")[-1])
    for child in node.children:
        _collect_calls(child, src, out)


def index_repo(root: Path) -> list[FnCard]:
    """遍历仓库全部 py 文件。"""
    cards: list[FnCard] = []
    for py in sorted(root.rglob("*.py")):
        if any(part in _SKIP_DIRS for part in py.parts):
            continue
        cards.extend(parse_python_file(py, root))
    return cards
