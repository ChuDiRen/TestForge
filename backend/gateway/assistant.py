"""AI 助手（对标 GitNexus 对话能力，落地到 TestForge 服务端设施）：

- GET  /api/assistant/threads              会话列表
- POST /api/assistant/threads              新建会话 {title?, repo_id?}
- DELETE /api/assistant/threads/{id}       删除会话（连带消息）
- GET  /api/assistant/threads/{id}/messages 历史消息
- POST /api/assistant/chat                 发消息（SSE 流式：tool_start/tool_end/token/done）

Agent 设计沿袭 GitNexus：迭代调查循环 + 强制引用 groundling 协议 + 工具集
（search/explore/read/impact/overview/cases，全部映射 TestForge 真实数据设施，
不做任何演示性硬编码）。工具循环在服务端完成——GitNexus 在浏览器内跑
LangGraph，本项目是中心化后端，协议等价但密钥不出服务端。
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncGenerator, Callable
from pathlib import Path

from fastapi import Request
from fastapi.responses import StreamingResponse
from pydantic import SecretStr

from gateway.main import ApiError, app, get_session, ok
from services.shared.models import (
    CallEdges,
    Cases,
    ChatMessage,
    ChatThread,
    Contracts,
    Defects,
    Functions,
    Repos,
    Requirements,
    WikiPages,
)

MAX_TOOL_ROUNDS = 8

# ---------------- 工具集（GitNexus 七工具的 TestForge 映射） ----------------


def _tool_search(repo_id: int) -> Callable[[dict], dict]:
    def run(args: dict) -> dict:
        from services.shared.rag import hybrid_search

        hits = hybrid_search(
            str(args.get("query") or ""),
            limit=min(int(args.get("limit") or 8), 20),
            repo_id=repo_id,
        )
        return {
            "results": [
                {
                    "title": h["title"],
                    "kind": h["kind"],
                    "repo_id": h["repo_id"],
                    "score": round(float(h["score"]), 3),
                    "excerpt": h["content"][:600],
                }
                for h in hits
            ]
        }

    return run


def _tool_explore(repo_id: int) -> Callable[[dict], dict]:
    def run(args: dict) -> dict:
        from services.shared.docgraph import kg_query

        res = kg_query(
            repo_id,
            str(args.get("question") or ""),
            mode=str(args.get("mode") or "mix"),
            limit=min(int(args.get("limit") or 6), 12),
        )
        return {
            "entities": [
                {"name": e["title"], "excerpt": e["content"][:300], "refs": e.get("meta", {}).get("refs", [])}
                for e in res.get("entities", [])
            ],
            "relations": [
                {"src": r["meta"].get("src"), "rtype": r["meta"].get("rtype"), "dst": r["meta"].get("dst")}
                for r in res.get("relations", [])
            ][:12],
        }

    return run


def _tool_read(repo_id: int) -> Callable[[dict], dict]:
    def run(args: dict) -> dict:
        rel = str(args.get("path") or "").replace("\\", "/").lstrip("/")
        if not rel or ".." in rel.split("/"):
            raise ValueError("path 非法：不允许目录穿越")
        with get_session() as sess:
            repo = sess.get(Repos, repo_id)
        if repo is None or not repo.local_path:
            raise ValueError("仓库不存在或未检出")
        root = Path(repo.local_path).resolve()
        target = (root / rel).resolve()
        if not str(target).startswith(str(root)):
            raise ValueError("path 越界")
        if not target.is_file():
            raise ValueError(f"文件不存在: {rel}")
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(int(args.get("start") or 1), 1)
        end = min(int(args.get("end") or start + 200), start + 400, len(lines))
        return {
            "path": rel,
            "total_lines": len(lines),
            "content": "\n".join(f"{i + 1}: {lines[i]}" for i in range(start - 1, end)),
        }

    return run


def _tool_impact(repo_id: int) -> Callable[[dict], dict]:
    def run(args: dict) -> dict:
        name = str(args.get("function") or "").strip()
        if not name:
            raise ValueError("function 参数必填")
        from services.shared.models import FnImpact

        with get_session() as sess:
            rows = (
                sess.query(FnImpact)
                .filter(FnImpact.repo_id == repo_id, FnImpact.fn_name.like(f"%{name}%"))
                .limit(5)
                .all()
            )
            callers = (
                sess.query(Functions.name, Functions.module)
                .join(CallEdges, CallEdges.caller_id == Functions.id)
                .filter(CallEdges.callee_id.in_(
                    sess.query(Functions.id).filter(Functions.repo_id == repo_id, Functions.name.like(f"%{name}%"))
                ))
                .distinct()
                .limit(15)
                .all()
            )
        return {
            "impacts": [
                {"function": r.fn_name, "blast_radius": r.reach_count, "score": round(float(r.score), 2)}
                for r in rows
            ],
            "direct_callers": [f"{m}.{n}" if m else n for n, m in callers],
        }

    return run


def _tool_overview(repo_id: int) -> Callable[[dict], dict]:
    def run(_args: dict) -> dict:
        with get_session() as sess:
            mods = (
                sess.query(Functions.module)
                .filter(Functions.repo_id == repo_id, Functions.module != "")
                .distinct()
                .all()
            )
            stats = {
                "模块数": len(mods),
                "函数数": sess.query(Functions).filter(Functions.repo_id == repo_id).count(),
                "wiki页数": sess.query(WikiPages).filter(WikiPages.repo_id == repo_id).count(),
                "需求数": sess.query(Requirements).filter(Requirements.repo_id == repo_id).count(),
                "用例数": sess.query(Cases).filter(Cases.repo_id == repo_id).count(),
                "缺陷数": sess.query(Defects).count(),  # 缺陷无 repo 维度，全库计数
                "契约数": sess.query(Contracts).filter(Contracts.provider_repo != "").count(),
            }
            sample_modules = sorted({m for (m,) in mods})[:30]
        return {"stats": stats, "sample_modules": sample_modules}
    return run


def _tool_cases(repo_id: int) -> Callable[[dict], dict]:
    def run(args: dict) -> dict:
        kw = str(args.get("keyword") or "").strip()
        with get_session() as sess:
            q = sess.query(Cases).filter(Cases.repo_id == repo_id)
            if kw:
                q = q.filter(Cases.title.like(f"%{kw}%") | Cases.target_function.like(f"%{kw}%"))
            rows = q.order_by(Cases.id.desc()).limit(10).all()
        return {
            "cases": [
                {
                    "code": c.code,
                    "title": c.title,
                    "layer": c.layer,
                    "status": c.status,
                    "target_function": c.target_function,
                }
                for c in rows
            ]
        }
    return run


TOOL_SPECS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "混合检索（向量+全文，RRF 融合）：搜索 Wiki 页、需求、缺陷、图谱实体等全部知识文档。发现类问题的起点。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索词"},
                    "limit": {"type": "integer", "description": "返回条数，默认 8"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "explore",
            "description": "知识图谱导航：按问题查实体与关系（local 实体级 / global 主题级 / mix 融合），适合追查『X 和 Y 什么关系』『Z 涉及哪些模块』。",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "mode": {"type": "string", "enum": ["local", "global", "mix"]},
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "读取仓库源码文件内容（带行号）。检索命中后必须 read 验证真实代码，不要凭名字猜行为。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "仓库相对路径，如 services/shared/db.py"},
                    "start": {"type": "integer"},
                    "end": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "impact",
            "description": "变更影响面：查函数的 blast radius（上游受波及函数数）与直接调用方。",
            "parameters": {
                "type": "object",
                "properties": {"function": {"type": "string", "description": "函数名，支持模糊"}},
                "required": ["function"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "overview",
            "description": "仓库概览：模块/函数/Wiki/需求/用例/缺陷/契约的规模统计与模块清单。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cases",
            "description": "查测试用例：按关键词/目标函数搜用例库（层别、状态、被测函数）。",
            "parameters": {
                "type": "object",
                "properties": {"keyword": {"type": "string"}},
                "required": ["keyword"],
            },
        },
    },
]


def _tools_for(repo_id: int) -> tuple[dict[str, Callable[[dict], dict]], list[dict]]:
    runners = {
        "search": _tool_search(repo_id),
        "explore": _tool_explore(repo_id),
        "read": _tool_read(repo_id),
        "impact": _tool_impact(repo_id),
        "overview": _tool_overview(repo_id),
        "cases": _tool_cases(repo_id),
    }
    return runners, json.loads(json.dumps(TOOL_SPECS))


# ---------------- System Prompt（GitNexus grounding 协议中文翻版） ----------------

SYSTEM_PROMPT = """你是 TestForge 的代码分析助手，拥有平台知识库与仓库源码的完整访问能力。回答必须有据可查。

## 强制引用（Grounding）
每个事实性结论都必须带引用，格式：`[[文件路径:起-止行]]`（如 [[services/shared/db.py:45-60]]）或 `[[Function:函数名]]`。
- 没有证据就明说「未检索到相关证据」，禁止编造。
- 引用必须是工具结果里真实出现过的路径/行号/函数名。

## 调查循环
你是调查者，不是一次性问答机：
1. 规划——先说一句要查什么、为什么。
2. 执行——调用工具收集证据。
3. 追溯——search 命中后用 read 验证源码；涉及关系用 explore；评估改动风险用 impact。
4. 交叉验证——结论前用第二个工具确认，不要只凭文档摘要。
5. 落引用——每个发现都标注 [[路径:行]] 或 [[Function:名]]。

每次工具调用前用一句话说明意图。工具失败时修正参数重试，不要停在报错上。

## 工具策略
- 发现类问题（是什么/有哪些）→ search / overview
- 关系类问题（谁调用/影响谁）→ explore / impact
- 结论前验证 → read（必用，别凭名字猜行为）
- 测试覆盖情况 → cases

## 输出风格
- 直接、无客套。中文回答。
- 对比/排名用表格；流程/架构用 mermaid 图（节点标签不带特殊字符）。
- 结尾给 **TL;DR** 一句话总结。
- 回答长度与问题复杂度匹配。"""

CITE_RE = re.compile(r"\[\[([^\]:]+):([^\]]+)\]\]")


def _extract_citations(text: str) -> list[dict]:
    seen: dict[str, dict] = {}
    for m in CITE_RE.finditer(text):
        key = m.group(0)
        if key not in seen:
            seen[key] = {"ref": m.group(1), "locator": m.group(2)}
    return list(seen.values())[:20]


# ---------------- Agent 循环（服务端 LangChain 工具循环 + SSE） ----------------


def _history(thread_id: int, limit: int = 12) -> list[tuple[str, str]]:
    with get_session() as sess:
        rows = (
            sess.query(ChatMessage)
            .filter(ChatMessage.thread_id == thread_id)
            .order_by(ChatMessage.id.desc())
            .limit(limit)
            .all()
        )
        return [(r.role, r.content) for r in reversed(rows)]


def _llm_model():
    from langchain_deepseek import ChatDeepSeek

    from services.shared.config import get_settings

    s = get_settings()
    if not s.llm_api_key:
        raise ApiError(400, "LLM_API_KEY 未配置：请在 .env 填入 DeepSeek API Key", 400)
    return ChatDeepSeek(
        model=(s.llm_model_query or s.llm_model),
        api_key=SecretStr(s.llm_api_key),
        base_url=s.llm_base_url,
        streaming=True,
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _agent_stream(thread: ChatThread, content: str) -> AsyncGenerator[str, None]:
    """工具循环 + 流式输出。事件：tool_start / tool_end / token / done / error。"""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    repo_id = thread.repo_id or 0
    try:
        with get_session() as sess:
            if repo_id and sess.get(Repos, repo_id) is None:
                repo_id = 0
        runners, specs = _tools_for(repo_id)
        model = _llm_model().bind_tools(specs)

        scope = f"（当前对话绑定仓库 repo_id={repo_id}）" if repo_id else "（未绑定仓库，工具将跨全库检索）"
        messages: list = [SystemMessage(content=SYSTEM_PROMPT + f"\n\n当前范围：{scope}")]
        for role, hist in _history(thread.id):
            messages.append(HumanMessage(content=hist) if role == "user" else AIMessage(content=hist))
        messages.append(HumanMessage(content=content))

        tool_events: list[dict] = []
        final_text = ""
        for _round in range(MAX_TOOL_ROUNDS):
            stream = model.astream(messages)
            ai_msg: AIMessage | None = None
            async for chunk in stream:
                if ai_msg is None:
                    ai_msg = chunk
                else:
                    ai_msg = ai_msg + chunk
            if ai_msg is None:
                break
            messages.append(ai_msg)
            if ai_msg.content:
                piece = str(ai_msg.content)
                final_text += piece
                yield _sse("token", {"text": piece})
            calls = list(ai_msg.tool_calls or [])
            if not calls:
                break
            for call in calls:
                name = call.get("name", "")
                args = call.get("args") or {}
                t0 = time.time()
                yield _sse("tool_start", {"name": name, "args": args})
                try:
                    if name not in runners:
                        raise ValueError(f"未知工具 {name}")
                    result = runners[name](args)
                    summary = json.dumps(result, ensure_ascii=False)
                    summary = summary[:150] + ("…" if len(summary) > 150 else "")
                    ms = int((time.time() - t0) * 1000)
                    tool_events.append({"name": name, "args": args, "summary": summary, "ms": ms})
                    yield _sse("tool_end", {"name": name, "summary": summary, "ms": ms})
                    messages.append(ToolMessage(content=json.dumps(result, ensure_ascii=False)[:12000], tool_call_id=call.get("id", "")))
                except Exception as exc:  # noqa: BLE001 —— 工具错误回填给模型自行修正
                    err = f"工具执行失败: {exc}"
                    ms = int((time.time() - t0) * 1000)
                    tool_events.append({"name": name, "args": args, "summary": err, "ms": ms})
                    yield _sse("tool_end", {"name": name, "summary": err, "ms": ms})
                    messages.append(ToolMessage(content=err, tool_call_id=call.get("id", "")))
        else:
            final_text += "\n\n（已达单次工具调用轮数上限，基于已有证据作答。）"

        citations = _extract_citations(final_text)
        with get_session() as sess:
            sess.add(
                ChatMessage(
                    thread_id=thread.id,
                    role="assistant",
                    content=final_text,
                    tool_events=json.dumps(tool_events, ensure_ascii=False),
                    citations=json.dumps(citations, ensure_ascii=False),
                )
            )
            sess.commit()
        yield _sse("done", {"citations": citations, "tool_events": tool_events})
    except ApiError as exc:
        yield _sse("error", {"message": exc.message})
    except Exception as exc:  # noqa: BLE001
        yield _sse("error", {"message": f"助手执行失败: {exc}"})


# ---------------- 路由 ----------------


@app.get("/api/assistant/threads")
def assistant_threads():
    with get_session() as sess:
        rows = sess.query(ChatThread).order_by(ChatThread.updated_at.desc()).limit(50).all()
        return ok({"threads": [{"id": t.id, "title": t.title, "repo_id": t.repo_id, "updated_at": t.updated_at.isoformat()} for t in rows]})


@app.post("/api/assistant/threads")
async def assistant_thread_create(request: Request):
    body = await request.json()
    with get_session() as sess:
        t = ChatThread(
            title=str(body.get("title") or "新对话")[:128],
            repo_id=int(body.get("repo_id") or 0),
            created_by=str(body.get("username") or ""),
        )
        sess.add(t)
        sess.commit()
        return ok({"id": t.id, "title": t.title, "repo_id": t.repo_id})


@app.delete("/api/assistant/threads/{thread_id}")
def assistant_thread_delete(thread_id: int):
    with get_session() as sess:
        sess.query(ChatMessage).filter(ChatMessage.thread_id == thread_id).delete()
        n = sess.query(ChatThread).filter(ChatThread.id == thread_id).delete()
        sess.commit()
    if not n:
        raise ApiError(404, "会话不存在", 404)
    return ok({"deleted": n})


@app.get("/api/assistant/threads/{thread_id}/messages")
def assistant_messages(thread_id: int):
    with get_session() as sess:
        rows = (
            sess.query(ChatMessage)
            .filter(ChatMessage.thread_id == thread_id)
            .order_by(ChatMessage.id.asc())
            .limit(200)
            .all()
        )
        return ok(
            {
                "messages": [
                    {
                        "id": r.id,
                        "role": r.role,
                        "content": r.content,
                        "tool_events": json.loads(r.tool_events or "[]"),
                        "citations": json.loads(r.citations or "[]"),
                        "created_at": r.created_at.isoformat(),
                    }
                    for r in rows
                ]
            }
        )


@app.post("/api/assistant/chat")
async def assistant_chat(request: Request):
    """发消息并流式接收回答（SSE）。body {thread_id, content}。"""
    body = await request.json()
    thread_id = int(body.get("thread_id") or 0)
    content = str(body.get("content") or "").strip()
    if not thread_id or not content:
        raise ApiError(400, "thread_id 与 content 必填", 400)
    with get_session() as sess:
        thread = sess.get(ChatThread, thread_id)
        if thread is None:
            raise ApiError(404, "会话不存在", 404)
        sess.add(ChatMessage(thread_id=thread.id, role="user", content=content))
        if thread.title == "新对话":
            thread.title = content[:40]
        sess.commit()

    async def gen():
        async for chunk in _agent_stream(thread, content):
            yield chunk

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
