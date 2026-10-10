"""LLM 通道：DeepSeek/智能体链路的统一异常 + 角色路由 + 单轮对话 + 流式。

角色路由（LightRAG 四角色）：不同环节用不同强度的模型——
- extract：批量抽取/摘要（快、不开思考，成本低）
- query：最终生成/报告（可用 deepseek-reasoner 等强模型）
- keyword：检索词提取（轻量低延迟）
- vlm：多模态图片描述（vision 模型，独立网关配置）

系统无任何确定性假实现：Key 未配置、输出非法时一律显式报错。
"""

from __future__ import annotations

from collections.abc import Iterator

ROLES = ("extract", "query", "keyword", "vlm")


class LLMError(RuntimeError):
    pass


def model_for(role: str) -> str:
    """角色 → 模型名；角色专属配置为空时回退 llm_model。"""
    from app.core.config import get_settings

    s = get_settings()
    if role == "extract":
        return s.llm_model_extract or s.llm_model
    if role == "keyword":
        return s.llm_model_keyword or s.llm_model
    if role == "query":
        return s.llm_model_query or s.llm_model
    if role == "vlm":
        return s.vlm_model or s.llm_model
    raise LLMError(f"未知 LLM 角色: {role}（支持 {ROLES}）")


def _client(role: str):  # type: ignore[no-untyped-def]
    from langchain_deepseek import ChatDeepSeek
    from pydantic import SecretStr

    from app.core.config import get_settings

    s = get_settings()
    if role == "vlm":
        # VLM 角色可走独立网关（vision 模型常不与文本模型同域）
        base_url = s.vlm_base_url or s.llm_base_url
        api_key = s.vlm_api_key or s.llm_api_key
        model_name = model_for("vlm")
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model_name, api_key=SecretStr(api_key), base_url=base_url, max_retries=1)
    if not s.llm_api_key:
        raise LLMError("LLM_API_KEY 未配置：请在 .env 填入 DeepSeek API Key（https://platform.deepseek.com）")
    return ChatDeepSeek(model=model_for(role), api_key=SecretStr(s.llm_api_key), base_url=s.llm_base_url)


def chat_once(prompt: str, system: str = "", role: str = "query") -> str:
    """单轮对话（按角色路由模型）；Key 未配置时显式报错。"""
    model = _client(role)
    messages = ([("system", system)] if system else []) + [("human", prompt)]
    resp = model.invoke(messages)
    return str(resp.content)


def chat_stream(prompt: str, system: str = "", role: str = "query") -> Iterator[str]:
    """流式单轮对话：逐 token 产出文本增量（SSE 查询回答用）。"""
    model = _client(role)
    messages = ([("system", system)] if system else []) + [("human", prompt)]
    for chunk in model.stream(messages):
        piece = chunk.content if hasattr(chunk, "content") else str(chunk)
        if piece:
            yield str(piece)
