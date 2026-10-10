"""Embedding 多 provider（LightRAG 对齐）：local（确定性 hash 词袋）| openai（OpenAI 兼容 /v1/embeddings）。

- local：256 维词袋 hash 向量，零外部依赖，行为与历史 rag.embed 完全一致（默认后端）；
- openai：langchain-openai 走任意 OpenAI 兼容端点（SiliconFlow bge-m3 / OpenAI / 内网网关），
  DB 持久缓存（embedding_cache 表）——同文本只付一次钱，重启不失效；
- 维度迁移：后端切换导致维度变化时，rag_documents 向量列自动 ALTER 并清空旧向量，
  由 kg-rebuild-vdb 重嵌全库（对齐 LightRAG lightrag-rebuild-vdb）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re

from services.shared.db import get_session
from services.shared.models import EmbeddingCache

log = logging.getLogger("shared.embedding")

LOCAL_DIM = 256
_TOKEN = re.compile(r"[A-Za-z_]{2,}|[\u4e00-\u9fff]")

_MEM_CACHE: dict[str, list[float]] = {}
_MEM_CACHE_MAX = 20_000


def backend() -> str:
    from services.shared.config import get_settings

    return (get_settings().embedding_backend or "local").strip().lower()


def dim() -> int:
    """当前后端向量维度：local 恒 256；openai 后端按配置模型（首次调用后确定，配置前按 256 建列）。"""
    if backend() == "local":
        return LOCAL_DIM
    from services.shared.config import get_settings

    named = (get_settings().embedding_model or "").lower()
    # 常见模型维度表：未列出的在首次真实调用后由 _learned_dim 记忆
    if "bge-m3" in named or "text-embedding-3-large" in named:
        return 1024
    if "text-embedding-3-small" in named:
        return 1536
    return _learned_dim() or LOCAL_DIM


_learned: int = 0


def _learned_dim() -> int:
    return _learned


def _hash_vec(text: str) -> list[float]:
    """确定性词袋 hash 向量（归一化），与 rag.embed 历史实现逐位一致。"""
    vec = [0.0] * LOCAL_DIM
    for tok in _TOKEN.findall((text or "").lower()):
        h = 0
        for ch in tok:
            h = (h * 131 + ord(ch)) & 0xFFFFFFFF
        vec[h % LOCAL_DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 6) for v in vec]


def _openai_embed(texts: list[str]) -> list[list[float]]:
    from langchain_openai import OpenAIEmbeddings
    from pydantic import SecretStr

    from services.shared.config import get_settings

    s = get_settings()
    if not s.embedding_api_key or not s.embedding_model:
        raise RuntimeError("EMBEDDING_BACKEND=openai 需要 EMBEDDING_API_KEY 与 EMBEDDING_MODEL（如 BAAI/bge-m3）")
    client = OpenAIEmbeddings(
        model=s.embedding_model,
        api_key=SecretStr(s.embedding_api_key),
        base_url=s.embedding_base_url or None,
        chunk_size=s.embedding_batch_size,
        check_embedding_ctx_length=False,  # 三方网关常不支持 tiktoken 计数
    )
    out = [list(map(float, v)) for v in client.embed_documents(texts)]
    global _learned
    if out and not _learned:
        _learned = len(out[0])
    return out


def _cache_key(text: str) -> str:
    from services.shared.config import get_settings

    s = get_settings()
    raw = f"{backend()}|{s.embedding_model}|{text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def embed(text: str) -> list[float]:
    """单文本向量（带进程内缓存；openai 后端再落 DB 缓存）。"""
    from services.shared.config import get_settings

    text = text or ""
    if backend() == "local":
        key = hashlib.md5(text.encode("utf-8")).hexdigest()
        hit = _MEM_CACHE.get(key)
        if hit is not None:
            return hit
        vec = _hash_vec(text)
        if len(_MEM_CACHE) >= _MEM_CACHE_MAX:
            _MEM_CACHE.clear()
        _MEM_CACHE[key] = vec
        return vec
    key = _cache_key(text)
    hit = _MEM_CACHE.get(key)
    if hit is not None:
        return hit
    with get_session() as sess:
        row = sess.get(EmbeddingCache, key)
        if row is not None:
            try:
                vec = json.loads(row.vec)
                if isinstance(vec, list) and vec:
                    _MEM_CACHE[key] = vec
                    return vec
            except json.JSONDecodeError:
                pass
    vec = _openai_embed([text])[0]
    _MEM_CACHE[key] = vec
    try:
        with get_session() as sess:
            sess.merge(EmbeddingCache(cache_key=key, model=get_settings().embedding_model, vec=json.dumps(vec)))
            sess.commit()
    except Exception as exc:  # noqa: BLE001  缓存写失败不阻塞主链路
        log.debug("embedding cache write failed: %s", exc)
    return vec


def embed_batch(texts: list[str]) -> list[list[float]]:
    """批量向量：openai 后端整批一次请求，local 直接循环。"""
    from services.shared.config import get_settings

    if backend() == "local":
        return [embed(t) for t in texts]
    todo = [(i, t) for i, t in enumerate(texts) if _cache_key(t) not in _MEM_CACHE]
    if not todo:
        return [embed(t) for t in texts]
    keys = [_cache_key(t) for _, t in todo]
    with get_session() as sess:
        rows = {r.cache_key: r.vec for r in sess.query(EmbeddingCache).filter(EmbeddingCache.cache_key.in_(keys)).all()} if keys else {}
    results: dict[int, list[float]] = {}
    missing: list[tuple[int, str]] = []
    for i, t in todo:
        k = _cache_key(t)
        raw = rows.get(k)
        if raw:
            try:
                v = json.loads(raw)
                if isinstance(v, list) and v:
                    results[i] = v
                    continue
            except json.JSONDecodeError:
                pass
        missing.append((i, t))
    if missing:
        global _learned
        vecs = _openai_embed([t for _, t in missing])
        for (i, t), v in zip(missing, vecs):
            results[i] = v
            _MEM_CACHE[_cache_key(t)] = v
        try:
            with get_session() as sess:
                for (i, t), v in zip(missing, vecs):
                    sess.merge(EmbeddingCache(cache_key=_cache_key(t), model=get_settings().embedding_model, vec=json.dumps(v)))
                sess.commit()
        except Exception as exc:  # noqa: BLE001
            log.debug("embedding cache write failed: %s", exc)
    return [results.get(i) or embed(t) for i, t in enumerate(texts)]


def clear_cache() -> int:
    """清空神经向量持久缓存（切模型后强制重算）。"""
    with get_session() as sess:
        n = sess.query(EmbeddingCache).delete(synchronize_session=False)
        sess.commit()
        return int(n)
