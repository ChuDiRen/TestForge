"""Token 计数（LightRAG 用 tiktoken，这里同源）：tiktoken 优先，缺失回退字符估算。

分块、上下文预算裁剪、抽取截断统一走这里，保证全链路口径一致。
"""

from __future__ import annotations

import functools

_enc: object | None = None


@functools.lru_cache(maxsize=1)
def _get_enc() -> object | None:
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception:  # noqa: BLE001  tiktoken 未安装/离线无缓存 → 字符估算
        return None


def count_tokens(text: str) -> int:
    """token 计数：tiktoken 精确计数；回退按 CJK 1 字≈1 token、拉丁 4 字符≈1 token 估算。"""
    text = text or ""
    enc = _get_enc()
    if enc is not None:
        try:
            return len(enc.encode(text))  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk + (len(text) - cjk) // 4 + 1


def truncate_tokens(text: str, max_tokens: int) -> str:
    """按 token 预算截断（不超预算地尽量保留）。"""
    if max_tokens <= 0:
        return ""
    enc = _get_enc()
    if enc is not None:
        try:
            ids = enc.encode(text or "")  # type: ignore[attr-defined]
            if len(ids) <= max_tokens:
                return text
            return enc.decode(ids[:max_tokens])  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    # 回退：CJK 密度决定字符/token 比例
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff") or 1
    ratio = len(text) / (cjk + (len(text) - cjk) / 4 + 1)
    return text[: int(max_tokens * ratio)]


def tail_tokens(text: str, max_tokens: int) -> str:
    """按 token 预算取尾部（分块 overlap 用）：二分找最长的 ≤max_tokens 后缀。"""
    if max_tokens <= 0:
        return ""
    text = text or ""
    if count_tokens(text) <= max_tokens:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi) // 2
        if count_tokens(text[mid:]) <= max_tokens:
            hi = mid
        else:
            lo = mid + 1
    return text[lo:]
