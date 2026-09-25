"""多语言 tree-sitter 索引：函数卡片（签名/源码/位置）+ 调用图。

语言覆盖由 tree-sitter-language-pack 提供，按扩展名映射语言；
每语言一份"函数节点类型 + 调用节点类型"配置表，新增语言只需加一行。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from tree_sitter import Parser
from tree_sitter_language_pack import get_language, get_parser

log = logging.getLogger("repo-svc.treesitter")

_SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".tox", "build", "dist", ".pytest_cache", "target", "vendor", "bin", "obj"}

# 扩展名 → 语言（tree-sitter-language-pack 语言名）
EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".go": "go",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".swift": "swift",
    ".sh": "bash",
    ".bash": "bash",
}


@dataclass
class LangConfig:
    """单语言的 tree-sitter 索引配置。"""

    ts_lang: str
    fn_types: tuple[str, ...]  # 函数/方法节点类型
    call_types: tuple[str, ...]  # 调用表达式节点类型


LANG_CONFIGS: dict[str, LangConfig] = {
    "python": LangConfig("python", ("function_definition",), ("call",)),
    "go": LangConfig("go", ("function_declaration", "method_declaration"), ("call_expression",)),
    "java": LangConfig("java", ("method_declaration", "constructor_declaration"), ("method_invocation",)),
    "javascript": LangConfig("javascript", ("function_declaration", "method_definition"), ("call_expression",)),
    "typescript": LangConfig("typescript", ("function_declaration", "method_definition", "function_signature"), ("call_expression",)),
    "tsx": LangConfig("tsx", ("function_declaration", "method_definition"), ("call_expression",)),
    "rust": LangConfig("rust", ("function_item",), ("call_expression",)),
    "c": LangConfig("c", ("function_definition",), ("call_expression",)),
    "cpp": LangConfig("cpp", ("function_definition",), ("call_expression",)),
    "csharp": LangConfig("csharp", ("method_declaration",), ("invocation_expression",)),
    "ruby": LangConfig("ruby", ("method", "singleton_method"), ("call",)),
    "php": LangConfig("php", ("function_definition", "method_declaration"), ("function_call_expression",)),
    "kotlin": LangConfig("kotlin", ("function_declaration",), ("call_expression",)),
    "swift": LangConfig("swift", ("function_declaration",), ("call_expression",)),
    "bash": LangConfig("bash", ("function_definition",), ("command",)),
}

# 语言可用性探测缓存（language pack 缺失时自动降级跳过）
_probed: dict[str, bool] = {}


def lang_of(path: str | Path) -> str | None:
    return EXT_TO_LANG.get(Path(path).suffix.lower())


def is_supported_file(path: str | Path) -> bool:
    return lang_of(path) is not None


def supported_extensions() -> set[str]:
    return set(EXT_TO_LANG)


def _lang_available(lang: str) -> bool:
    """肯定结果缓存；否定不缓存（避免偶发加载失败永久禁用某语言）。"""
    ok = _probed.get(lang)
    if ok is not None:
        return ok
    try:
        get_language(lang)
        _probed[lang] = True
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("tree-sitter 语言不可用，跳过: %s (%s)", lang, exc)
        return False


def _lang_config(lang: str) -> LangConfig | None:
    if lang not in LANG_CONFIGS or not _lang_available(lang):
        return None
    return LANG_CONFIGS[lang]


_PARSERS: dict[str, Parser] = {}


def _parser_for(lang: str) -> Parser | None:
    if not _lang_available(lang):
        return None
    if lang not in _PARSERS:
        try:
            _PARSERS[lang] = get_parser(lang)
        except Exception:  # noqa: BLE001
            log.warning("parser 创建失败: %s", lang)
            return None
    return _PARSERS[lang]


@dataclass
class FnCard:
    module: str
    name: str
    signature: str
    source: str
    file: str
    line: int
    docstring: str = ""
    is_public: bool = True
    language: str = ""
    calls: list[str] = field(default_factory=list)


def _module_of(path: Path, root: Path, lang: str) -> str:
    rel = path.relative_to(root).with_suffix("")
    parts = list(rel.parts)
    if lang == "python" and parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _node_src(src: bytes, node) -> str:  # type: ignore[no-untyped-def]
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _deep_first_identifier(node, src: bytes, depth: int = 0) -> str:  # type: ignore[no-untyped-def]
    """C/C++ 等无 name field 的语言：从 declarator 子树找第一个 identifier。"""
    if depth > 6:
        return ""
    for child in node.children:
        if "identifier" in child.type:
            return _node_src(src, child)
        found = _deep_first_identifier(child, src, depth + 1)
        if found:
            return found
    return ""


def _fn_name(node, src: bytes) -> str:  # type: ignore[no-untyped-def]
    name_node = node.child_by_field_name("name")
    if name_node is not None:
        return _node_src(src, name_node)
    decl = node.child_by_field_name("declarator")  # c/cpp
    if decl is not None:
        return _deep_first_identifier(decl, src)
    return ""


def _fn_params(node, src: bytes) -> str:  # type: ignore[no-untyped-def]
    params = node.child_by_field_name("parameters")
    if params is None:
        decl = node.child_by_field_name("declarator")  # c/cpp
        if decl is not None:
            for child in decl.children:
                if child.type == "function_declarator":
                    p = child.child_by_field_name("parameters")
                    if p is not None:
                        return _node_src(src, p)
            return ""
        # ruby/bash 无 parameters field
        return ""
    return _node_src(src, params)


def _fn_doc(node, src: bytes, lang: str) -> str:  # type: ignore[no-untyped-def]
    """python 取 docstring；其余语言取函数体首条注释或声明前的注释。"""
    body = node.child_by_field_name("body")
    if lang == "python" and body is not None and body.children:
        first = body.children[0]
        if first.type == "expression_statement" and first.children and first.children[0].type == "string":
            return _node_src(src, first.children[0]).strip()[:2000]
    if body is not None:
        for child in body.children:
            if child.type == "comment":
                return _node_src(src, child).strip()[:2000]
            break
    prev = node.prev_sibling
    if prev is not None and prev.type == "comment":
        return _node_src(src, prev).strip()[:2000]
    return ""


def _collect_calls(node, src: bytes, call_types: tuple[str, ...], out: list[str]) -> None:  # type: ignore[no-untyped-def]
    if node.type in call_types:
        name_node = node.child_by_field_name("function") or node.child_by_field_name("name") or node.child_by_field_name("method")
        if name_node is None:
            for child in node.children:
                if "identifier" in child.type:
                    name_node = child
                    break
        if name_node is not None:
            out.append(_node_src(src, name_node).split(".")[-1].split("::")[-1])
    for child in node.children:
        _collect_calls(child, src, call_types, out)


def parse_file(py_file: Path, root: Path) -> list[FnCard]:
    """解析单个源文件（按扩展名自动选语言）。"""
    lang = lang_of(py_file)
    if lang is None:
        return []
    cfg = _lang_config(lang)
    parser = _parser_for(lang)
    if cfg is None or parser is None:
        return []
    try:
        src = py_file.read_bytes()
        tree = parser.parse(src)
    except Exception as exc:  # noqa: BLE001
        log.warning("parse failed %s: %s", py_file, exc)
        return []

    module = _module_of(py_file, root, lang)
    cards: list[FnCard] = []

    def walk(node, prefix: str = "") -> None:  # type: ignore[no-untyped-def]
        for child in node.children:
            if child.type in cfg.fn_types:
                card = _card(child, src, module, py_file, root, lang, cfg, prefix)
                if card.name:
                    cards.append(card)
                continue  # 不深入函数体（闭包/嵌套函数不入库）
            cls = _class_name(child, src)
            if cls:
                walk(child, f"{prefix}{cls}.")  # 进入类体：方法名带类前缀
            else:
                walk(child, prefix)

    walk(tree.root_node)
    return cards


def _class_name(node, src: bytes) -> str:  # type: ignore[no-untyped-def]
    """类/结构体节点返回类名，否则空串。"""
    if node.type in ("class_definition", "class_declaration", "class_specifier", "struct_type", "implementation_definition"):
        n = node.child_by_field_name("name")
        return _node_src(src, n) if n is not None else ""
    return ""


def _card(node, src: bytes, module: str, path: Path, root: Path, lang: str, cfg: LangConfig, class_prefix: str) -> FnCard:  # type: ignore[no-untyped-def]
    name = _fn_name(node, src)
    params = _fn_params(node, src)
    body = node.child_by_field_name("body")
    calls: list[str] = []
    if body is not None:
        _collect_calls(body, src, cfg.call_types, calls)
    full_name = f"{class_prefix}{name}" if name else ""
    return FnCard(
        module=module,
        name=full_name,
        signature=f"{full_name}({params})" if params else full_name,
        source=_node_src(src, node),
        file=str(path.relative_to(root)).replace("\\", "/"),
        line=node.start_point[0] + 1,
        docstring=_fn_doc(node, src, lang),
        is_public=not name.startswith("_"),
        language=lang,
        calls=calls,
    )


def index_repo(root: Path) -> list[FnCard]:
    """遍历仓库全部受支持语言源文件。"""
    cards: list[FnCard] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in EXT_TO_LANG:
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        cards.extend(parse_file(path, root))
    return cards
