"""Wiki 分层摘要：repo 总览页 / 模块页 / 函数卡片的确定性 Markdown 渲染。"""

from __future__ import annotations

import logging

from app.services.repo.indexer import FnCard

log = logging.getLogger("wiki-builder.summarize")


def render_repo_page(repo_name: str, cards: list[FnCard], extra: str = "") -> str:
    mods = sorted({c.module for c in cards})
    lines = [
        f"# {repo_name} · 仓库总览",
        "",
        f"- 函数总数：{len(cards)}",
        f"- 模块数：{len(mods)}",
        f"- 模块列表：{', '.join(mods) or '-'}",
        "",
        "## 模块职责速览",
        "",
    ]
    for m in mods:
        mc = [c for c in cards if c.module == m]
        pub = [c for c in mc if not c.name.split(".")[-1].startswith("_")]
        lines.append(f"- **{m}**：{len(mc)} 函数，对外接口 {len(pub)} 个（如 {', '.join(c.name for c in pub[:3]) or '-'}）")
    if extra:
        lines += ["", extra]
    return "\n".join(lines) + "\n"


def render_module_page(module: str, cards: list[FnCard]) -> str:
    lines = [
        f"# 模块页 · {module}",
        "",
        "## 职责",
        "",
        f"模块包含 {len(cards)} 个函数，主要对外接口：",
        "",
    ]
    for c in cards:
        if c.name.split(".")[-1].startswith("_"):
            continue
        brief = (c.docstring or "").strip().splitlines()[0][:80] if c.docstring else "（无 docstring）"
        lines.append(f"- `{c.name}{c.signature}` — {brief}")
    lines += ["", "## 已知风险点", "", "- （由历史缺陷关联自动补充，见缺陷管理）", ""]
    return "\n".join(lines)


def render_function_page(card: FnCard, callers: list[str], callees: list[str]) -> str:
    lines = [
        f"# 函数卡片 · {card.name}",
        "",
        f"- 文件：`{card.file}:{card.line}`",
        f"- 签名：`{card.name}{card.signature}`",
        f"- 语言：{card.language or 'python'}",
        f"- 调用方：{', '.join(f'`{c}`' for c in callers) or '-'}",
        f"- 被调：{', '.join(f'`{c}`' for c in callees) or '-'}",
        "",
        "## docstring",
        "",
        (card.docstring or "（无）"),
        "",
        "## 源码（ground truth）",
        "",
        f"```{card.language or ''}",
        card.source,
        "```",
        "",
    ]
    return "\n".join(lines)
