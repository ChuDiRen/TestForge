"""LLM 客户端：DeepSeek（OpenAI 兼容 /chat/completions，json_object 结构化输出）。

所有结构化输出经 pydantic schema 校验后才返回；未配置 Key 时直接抛出
LLMError——系统不提供任何确定性假实现，数据不允许造假。
"""

import json
import logging
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from services.shared.config import get_settings

log = logging.getLogger("shared.llm")
T = TypeVar("T", bound=BaseModel)

SYSTEM_PRIORITY = (
    "你是资深测试架构师。上下文注入优先级写死：code > contract > wiki > trace > similar > bugs；"
    "与源码/契约冲突时，一律以源码/契约为准。只输出符合给定 JSON Schema 的 JSON，不要多余文本。"
)


class LLMError(RuntimeError):
    pass


class LLMClient:
    """chat_json(messages, schema) -> 经 pydantic 校验的 DeepSeek 结构化输出。"""

    def __init__(self) -> None:
        self.settings = get_settings()

    def chat_json(self, messages: list[dict[str, str]], schema: type[T]) -> T:
        return self._validate(self._loads(self._chat_raw(messages)), schema)

    def chat_text(self, messages: list[dict[str, str]]) -> str:
        return self._chat_raw(messages)

    def _chat_raw(self, messages: list[dict[str, str]]) -> str:
        s = self.settings
        if not s.llm_api_key:
            raise LLMError("LLM_API_KEY 未配置：请在 .env 填入 DeepSeek API Key（https://platform.deepseek.com）")
        resp = httpx.post(
            f"{s.llm_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {s.llm_api_key}"},
            json={
                "model": s.llm_model,
                "messages": [{"role": "system", "content": SYSTEM_PRIORITY}, *messages],
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
            },
            timeout=60.0,
        )
        if resp.status_code != 200:
            raise LLMError(f"DeepSeek HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()["choices"][0]["message"]["content"]

    @staticmethod
    def _loads(raw: str) -> Any:
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMError(f"LLM 输出不是合法 JSON: {exc}: {raw[:200]}") from exc

    @staticmethod
    def _validate(data: Any, schema: type[T]) -> T:
        try:
            return schema.model_validate(data)
        except ValidationError as exc:
            raise LLMError(f"结构化输出未通过 schema 校验: {exc}") from exc


def get_llm() -> LLMClient:
    return LLMClient()
