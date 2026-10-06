"""LLM 通道：DeepSeek/智能体链路的统一异常 + 角色路由 + 单轮对话。

角色路由（LightRAG 式）：不同环节用不同强度的模型——
- extract：批量抽取/摘要（快、不开思考，成本低）
- query：最终生成/报告（可用 deepseek-reasoner 等强模型）
- keyword：检索词提取（轻量低延迟）

系统无任何确定性假实现：Key 未配置、输出非法时一律显式报错。
"""

from __future__ import annotations

ROLES = ("extract", "query", "keyword")


class LLMError(RuntimeError):
    pass


def model_for(role: str) -> str:
    """角色 → 模型名；角色专属配置为空时回退 llm_model。"""
    from services.shared.config import get_settings

    s = get_settings()
    if role == "extract":
        return s.llm_model_extract or s.llm_model
    if role == "keyword":
        return s.llm_model_keyword or s.llm_model
    if role == "query":
        return s.llm_model_query or s.llm_model
    raise LLMError(f"未知 LLM 角色: {role}（支持 {ROLES}）")


def chat_once(prompt: str, system: str = "", role: str = "query") -> str:
    """单轮 DeepSeek 对话（按角色路由模型）；Key 未配置时显式报错。"""
    from services.shared.config import get_settings

    s = get_settings()
    if not s.llm_api_key:
        raise LLMError("LLM_API_KEY 未配置：请在 .env 填入 DeepSeek API Key（https://platform.deepseek.com）")
    from langchain_deepseek import ChatDeepSeek
    from pydantic import SecretStr

    model = ChatDeepSeek(model=model_for(role), api_key=SecretStr(s.llm_api_key), base_url=s.llm_base_url)
    messages = ([("system", system)] if system else []) + [("human", prompt)]
    resp = model.invoke(messages)
    return str(resp.content)
