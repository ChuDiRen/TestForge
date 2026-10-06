"""规划智能体：deepagents 框架 + DeepSeek 大模型。

智能体带领域工具（读源码 / 模块清单 / 调用图），自主探索被测函数上下文后
产出用例清单（只设计输入与打桩；期望值由探针对真实代码执行捕获）。
"""

from __future__ import annotations

import json
import logging

from services.shared.config import get_settings
from services.shared.llm import LLMError
from services.testgen_svc.fninfo import FnInfo
from services.testgen_svc.schemas import CasePlan

log = logging.getLogger("testgen.agent")

SYSTEM_PROMPT = (
    "你是资深测试架构师。你的任务：为指定函数设计单测用例清单。\n"
    "规则：\n"
    "1. 先用工具阅读函数源码与所在模块，再设计用例；\n"
    "2. 覆盖 normal/boundary/exception/permission 四类，共 4~8 条；\n"
    "3. 每条只含 id/title/category/covers/input/patches——不要编造期望值，"
    "期望值将由系统对真实代码执行捕获；\n"
    "4. input 必须是 JSON 可序列化的 kwargs 字面量；patches 仅在依赖外部模块时使用；\n"
    "5. 最终回复必须只包含一个 JSON 对象："
    '{"target": "...", "module": "...", "cases": [{"id": "TC-001", ...}]}，不要多余文本。'
)


def _chat_model():  # type: ignore[no-untyped-def]
    s = get_settings()
    if not s.llm_api_key:
        raise LLMError("LLM_API_KEY 未配置：请在 .env 填入 DeepSeek API Key（https://platform.deepseek.com）")
    from langchain_deepseek import ChatDeepSeek
    from pydantic import SecretStr

    from services.shared.llm import model_for

    # 智能体规划 = query 角色（强模型可插拔：llm_model_query 优先）
    return ChatDeepSeek(model=model_for("query"), api_key=SecretStr(s.llm_api_key), base_url=s.llm_base_url)


def _build_tools(fn: FnInfo, callers: list[str], callees: list[str], siblings: list[dict]) -> list:
    from langchain_core.tools import tool

    @tool
    def read_fn_source() -> str:
        """读取被测函数的完整源码与签名。"""
        return f"# {fn.module}.{fn.name}\n{fn.source}\n# docstring: {fn.docstring or '（无）'}"

    @tool
    def read_call_graph() -> str:
        """读取该函数的调用关系（谁调用它 / 它调用谁）。"""
        return f"调用方: {callers or '无'}\n被调方: {callees or '无'}"

    @tool
    def list_module_functions() -> str:
        """列出同模块其他函数的名字与文档（了解模块职责）。"""
        lines = [f"- {x['name']}: {x['docstring']}" for x in siblings]
        return "\n".join(lines) or "（同模块无其他函数）"

    return [read_fn_source, read_call_graph, list_module_functions]


def plan_with_deepagent(
    fn: FnInfo,
    target: str,
    callers: list[str] | None = None,
    callees: list[str] | None = None,
    siblings: list[dict] | None = None,
) -> CasePlan:
    """用 deepagents 智能体（DeepSeek 大模型）规划用例清单。"""
    try:
        from deepagents import create_deep_agent
    except ImportError as exc:  # noqa: BLE001
        raise LLMError("deepagents 未安装：uv add deepagents") from exc

    agent = create_deep_agent(
        model=_chat_model(),
        tools=_build_tools(fn, callers or [], callees or [], siblings or []),
        system_prompt=SYSTEM_PROMPT,
    )
    task = (
        f"为函数 {target}（模块 {fn.module}）设计 4~8 条单测用例。"
        "先用工具阅读源码与上下文，再按要求输出 JSON。"
    )
    result = agent.invoke({"messages": [{"role": "user", "content": task}]}, config={"recursion_limit": 60})

    raw = result["messages"][-1].content
    if isinstance(raw, list):  # 多模态消息兼容
        raw = "".join(part.get("text", "") for part in raw if isinstance(part, dict))
    plan = CasePlan.model_validate(_extract_json(str(raw)))
    if not plan.cases:
        raise LLMError("智能体未产出任何用例")
    log.info("deepagent 规划 %s: %d 条用例", target, len(plan.cases))
    return plan


def _extract_json(raw: str) -> dict:
    """从智能体回复中提取 JSON 对象（容忍 ```json 围栏与前后说明文本）。"""
    text = raw.strip()
    if "```" in text:
        for seg in text.split("```"):
            seg = seg.removeprefix("json").strip()
            if seg.startswith("{"):
                text = seg
                break
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise LLMError(f"智能体回复中未找到 JSON: {text[:200]}")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError(f"智能体 JSON 非法: {exc}: {text[start : start + 200]}") from exc
