"""LLM 客户端：OpenAI 兼容 API（real）+ 确定性 mock 双实现。

mock 模式按 prompt 中的任务标记 [MOCK:<task>] 返回固定样例，
保证 LLM_MODE=mock 且无 API Key 时全流程可跑通。
所有结构化输出经 pydantic schema 校验后才返回。
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


def _extract_mock_task(messages: list[dict[str, str]]) -> str:
    for m in messages:
        if "[MOCK:" in m.get("content", ""):
            content = m["content"]
            start = content.index("[MOCK:") + 6
            end = content.index("]", start)
            return content[start:end]
    return "unknown"


class LLMClient:
    """chat_json(messages, schema) -> 经校验的模型实例。"""

    def __init__(self) -> None:
        self.settings = get_settings()

    @property
    def is_mock(self) -> bool:
        return self.settings.llm_mode == "mock"

    def chat_json(self, messages: list[dict[str, str]], schema: type[T], mock: Any = None) -> T:
        """返回经 pydantic 校验的结构化输出。mock=mock 模式下的样例数据（dict/str）。"""
        if self.is_mock:
            data = mock() if callable(mock) else mock
            if isinstance(data, str):
                data = json.loads(data)
            return self._validate(data, schema)
        raw = self._chat_raw(messages)
        return self._validate(self._loads(raw), schema)

    def chat_text(self, messages: list[dict[str, str]], mock: str = "") -> str:
        if self.is_mock:
            return mock
        return self._chat_raw(messages)

    # ---------- real ----------
    def _chat_raw(self, messages: list[dict[str, str]]) -> str:
        s = self.settings
        headers = {"Authorization": f"Bearer {s.llm_api_key}"} if s.llm_api_key else {}
        body = {
            "model": s.llm_model,
            "messages": [{"role": "system", "content": SYSTEM_PRIORITY}, *messages],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        resp = httpx.post(
            f"{s.llm_base_url.rstrip('/')}/chat/completions",
            headers=headers,
            json=body,
            timeout=60.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    # ---------- validate ----------
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


def mock_task(name: str) -> str:
    """在 user prompt 中打 mock 任务标记。"""
    return f"[MOCK:{name}]"
