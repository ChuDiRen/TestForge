"""LLM 通道：DeepSeek/智能体链路的统一异常 + 单轮对话。

系统无任何确定性假实现：Key 未配置、输出非法时一律显式报错。
"""


class LLMError(RuntimeError):
    pass


def chat_once(prompt: str, system: str = "") -> str:
    """单轮 DeepSeek 对话（缺陷修复建议等轻量场景）；Key 未配置时显式报错。"""
    from services.shared.config import get_settings

    s = get_settings()
    if not s.llm_api_key:
        raise LLMError("LLM_API_KEY 未配置：请在 .env 填入 DeepSeek API Key（https://platform.deepseek.com）")
    from langchain_deepseek import ChatDeepSeek
    from pydantic import SecretStr

    model = ChatDeepSeek(model=s.llm_model, api_key=SecretStr(s.llm_api_key), base_url=s.llm_base_url)
    messages = ([("system", system)] if system else []) + [("human", prompt)]
    resp = model.invoke(messages)
    return str(resp.content)
