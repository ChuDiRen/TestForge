"""Wiki 分层摘要：repo 总览页 / 模块页 / 函数卡片（mock=确定性模板，real=LLM 润色）。"""

from __future__ import annotations

import logging

from services.repo_svc.indexer import FnCard
from services.shared.llm import LLMClient, mock_task

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
        f"- 调用方：{', '.join(f'`{c}`' for c in callers) or '-'}",
        f"- 被调：{', '.join(f'`{c}`' for c in callees) or '-'}",
        "",
        "## docstring",
        "",
        (card.docstring or "（无）"),
        "",
        "## 源码（ground truth）",
        "",
        "```python",
        card.source,
        "```",
        "",
    ]
    return "\n".join(lines)


def polish(llm: LLMClient, markdown: str, level: str) -> str:
    """real 模式下用 LLM 润色摘要；mock 原样返回（确定性）。"""
    if llm.is_mock:
        return markdown
    out = llm.chat_text(
        [{"role": "user", "content": f"{mock_task('wiki')} 请为以下 {level} Wiki 页面润色补充'职责/业务规则/风险'三节，保持 Markdown 结构与事实不变：\n{markdown[:6000]}"}],
        mock=markdown,
    )
    return out or markdown
