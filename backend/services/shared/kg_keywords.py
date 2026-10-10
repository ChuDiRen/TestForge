"""双层关键词抽取（LightRAG 对齐）：high-level（主题/概念）+ low-level（具体实体/术语）分头召回。

- local 检索吃 low-level 关键词（找具体实体），global 检索吃 high-level 关键词（找主题关系链）；
- LLM keyword 角色缓存于 llm_cache；Key 未配置回退确定性 tokenize（query 永不因无 Key 失败）。
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger("shared.kg_keywords")

_HIGH_SYSTEM = (
    "你是检索关键词专家。从问题中提取「高层主题关键词」：跨场景的概念、业务域、"
    "抽象主题（如：支付安全、权限模型、数据一致性）。3~6 个，中文原词/英文小写，"
    "逗号分隔，直接输出不要解释。"
)
_LOW_SYSTEM = (
    "你是检索关键词专家。从问题中提取「低层实体关键词」：问题里具体提到的实体、"
    "函数/接口/表名、专有名词（如：submit_payment、订单表、幂等 token）。3~8 个，"
    "中文原词/英文小写，逗号分隔，直接输出不要解释。"
)


def _clean(raw: str, fallback_tokens: str) -> str:
    kw = re.sub(r"[^\w\u4e00-\u9fff,\s，]", " ", raw or "")
    parts = [p.strip() for p in re.split(r"[,，]", kw) if p.strip()]
    if parts:
        return " ".join(parts[:8])
    return fallback_tokens


def dual_keywords(question: str) -> dict[str, str]:
    """返回 {high_level, low_level}。无 Key/调用失败时两路均回退确定性 tokenize。"""
    from services.shared.rag import tokenize

    fallback = tokenize(question) or question
    out = {"high_level": fallback, "low_level": fallback}
    try:
        from services.shared.llm_cache import chat_cached

        raw_h = chat_cached(f"问题：{question}", system=_HIGH_SYSTEM, role="keyword")
        out["high_level"] = _clean(raw_h, fallback)
        raw_l = chat_cached(f"问题：{question}", system=_LOW_SYSTEM, role="keyword")
        out["low_level"] = _clean(raw_l, fallback)
    except Exception as exc:  # noqa: BLE001
        log.debug("dual keywords fallback to tokenize: %s", exc)
    return out
