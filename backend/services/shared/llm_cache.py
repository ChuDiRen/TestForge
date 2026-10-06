"""LLM 抽取缓存：内容 hash 键控（LightRAG 增量更新复用 LLM 缓存的思路）。

同一 (role, model, system, prompt) 只付一次 token；增量重建（wiki 页未变、
缺陷未变、需求未变）时命中缓存零成本。缓存落库持久化，进程重启不失效。
"""

from __future__ import annotations

import hashlib
import logging

from services.shared.db import get_session
from services.shared.models import LlmCache

log = logging.getLogger("shared.llm_cache")


def cache_key(role: str, system: str, prompt: str) -> str:
    from services.shared.llm import model_for

    raw = f"{role}|{model_for(role)}|{system}|{prompt}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def chat_cached(prompt: str, system: str = "", role: str = "extract") -> str:
    """带持久缓存的 LLM 单轮对话：命中直接返回，未命中调用并回填。"""
    from services.shared.llm import chat_once, model_for  # 调用时绑定（可测试性）

    key = cache_key(role, system, prompt)
    with get_session() as sess:
        row = sess.get(LlmCache, key)
        if row is not None:
            return row.response
    resp = chat_once(prompt, system=system, role=role)
    with get_session() as sess:
        sess.merge(
            LlmCache(
                cache_key=key,
                role=role,
                model=model_for(role),
                prompt=f"{system}\n---\n{prompt}"[:2000],
                response=resp,
            )
        )
        sess.commit()
    return resp


def cache_stats() -> dict:
    """缓存台账：命中可省的重复调用规模。"""
    from sqlalchemy import func

    with get_session() as sess:
        total = sess.query(func.count()).select_from(LlmCache).scalar() or 0
        by_role = {k: v for k, v in sess.query(LlmCache.role, func.count()).group_by(LlmCache.role).all()}
    return {"total": int(total), "by_role": by_role}


def clear_cache(role: str = "") -> int:
    """清缓存（角色可选）：抽取结果污染时强制重算。"""
    with get_session() as sess:
        q = sess.query(LlmCache)
        if role:
            q = q.filter(LlmCache.role == role)
        n = q.delete(synchronize_session=False)
        sess.commit()
        return int(n)


__all__ = ["chat_cached", "cache_key", "cache_stats", "clear_cache"]
