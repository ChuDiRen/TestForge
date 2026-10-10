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

from app.main import ApiError, app, get_session, ok
from app.models import (
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
from app.schemas.assistant import AssistantChatIn, AssistantThreadCreateIn

MAX_TOOL_ROUNDS = 8

# ---------------- 工具集（GitNexus 七工具的 TestForge 映射） ----------------


def _tool_search(repo_id: int) -> Callable[[dict], dict]:
    def run(args: dict) -> dict:
        from app.services.knowledge.rag import hybrid_search

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
        from app.services.knowledge.docgraph import kg_query

        res = kg_query(
            repo_id,
            str(args.get("question") or ""),
            mode=str(args.get("mode") or "mix"),
            limit=min(int(args.get("limit") or 6), 12),
        )
        return {
            "entities": [
                {"name": e["title"], "excerpt": e["content"][:300], "refs": e.get("meta", {}).get("source_refs", [])}
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
        if rel.startswith("repo/"):
            # 与内置文件工具的 /repo/ 只读挂载同语义：从仓库检出目录读
            rel = rel[len("repo/"):]
            from pathlib import Path as _P
            root = _repo_fs_root(repo_id)
            if not root:
                raise ValueError("当前会话未绑定已检出的仓库")
            target = (_P(root) / rel).resolve()
            if not str(target).startswith(str(_P(root).resolve())):
                raise ValueError("path 越界")
            if not target.is_file():
                raise ValueError(f"文件不存在: {rel}")
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
            start = max(int(args.get("start") or 1), 1)
            end = min(int(args.get("end") or start + 200), start + 400, len(lines))
            return {"path": rel, "total_lines": len(lines), "content": "\n".join(f"{i + 1}: {lines[i]}" for i in range(start - 1, end))}
        rel = rel.lstrip("/")
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
        from app.models import FnImpact

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


def _tool_changes(repo_id: int) -> Callable[[dict], dict]:
    def run(args: dict) -> dict:
        from app.services.repo.changes import detect_changes

        try:
            res = detect_changes(repo_id, scope=str(args.get("scope") or "unstaged"))
        except FileNotFoundError as e:
            return {"error": str(e)}
        return {
            "scope": res["scope"],
            "changed_files": res["changed_files"],
            "affected_functions": res["affected_functions"],
            "functions": [
                {"name": f["name"], "module": f["module"], "callers": f["direct_callers"][:5], "risk": f["risk"]}
                for f in res["functions"][:15]
            ],
        }

    return run


def _tool_trace(repo_id: int) -> Callable[[dict], dict]:
    def run(args: dict) -> dict:
        src = str(args.get("src") or "").strip()
        dst = str(args.get("dst") or "").strip()
        if not src or not dst:
            raise ValueError("src / dst 参数必填")
        from app.db.session import get_session as _gs
        from app.models import Functions as _Fn
        from app.services.repo.impact import trace_path

        rid = repo_id
        if not rid:
            with _gs() as s:
                row = s.query(_Fn).filter(_Fn.name == src).order_by(_Fn.id.desc()).first()
                rid = row.repo_id if row else 0
        res = trace_path(rid, src, dst) if rid else None
        if res is None:
            return {"error": f"起点或终点未索引（repo={rid or '未知'}）", "hint": "先确认函数名，可用 search 检索"}
        if not res["found"]:
            return {"found": False, "src": src, "dst": dst, "conclusion": f"{src} 到 {dst} 在调用图上不存在路径（深度≤10）"}
        return {
            "found": True,
            "length": res["length"],
            "path": " → ".join([res["hops"][0]["from"]] + [h["to"] for h in res["hops"]]) if res["hops"] else src,
            "hops": res["hops"],
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


# ---------------- 写工具（闭环：问答 → 驱动平台动作；仅 admin） ----------------


def _require_admin(role: str) -> None:
    if role != "admin":
        raise PermissionError("当前账号为只读权限，无法执行该写操作；请联系管理员或在管理员账号下重试")


def _tool_generate_case(repo_id: int, role: str) -> Callable[[dict], dict]:
    def run(args: dict) -> dict:
        _require_admin(role)
        from app.api.generations import new_generation

        layer = str(args.get("layer") or "ut")
        if layer not in ("ut", "fn", "api", "e2e"):
            raise ValueError(f"layer 仅支持 ut/fn/api/e2e，收到 {layer}")

        # web 层（api/e2e）：目标由运行中网关的实时契约自动发现，无需函数名
        if layer in ("api", "e2e"):
            with get_session() as sess:
                repo = sess.get(Repos, repo_id) if repo_id else sess.query(Repos).order_by(Repos.id.desc()).first()
            if repo is None:
                raise ValueError("尚未接入任何仓库，无法确定生成范围；请先在「仓库接入」接入仓库")
            res = new_generation({"function": f"web-{layer}", "repo_id": repo.id, "layer": layer})
            return {
                "generation_code": res.get("generation_id"),
                "job_code": res.get("job_code"),
                "repo_id": repo.id,
                "note": f"{layer} 生成任务已入队：实时抓取运行中网关 OpenAPI 契约后生成，完成后用例进入用例库 {layer} 层",
            }

        fn = str(args.get("function") or "").strip()
        if not fn:
            raise ValueError("function 必填，可先用 search/cases 工具确认函数名")
        # 会话未绑仓库时从函数索引反查目标函数真实所属仓库，否则生成管线无上下文
        with get_session() as sess:
            f = (
                sess.query(Functions)
                .filter(Functions.name == fn)
                .order_by(Functions.id.desc())
                .first()
                or sess.query(Functions)
                .filter(Functions.name.like(f"%{fn}%"))
                .order_by(Functions.id.desc())
                .first()
            )
        eff_repo = repo_id or (f.repo_id if f is not None else 0)
        if not eff_repo:
            raise ValueError(f"索引中找不到函数 {fn}，请确认仓库名（可先用 search 工具检索）")
        res = new_generation({"function": fn, "repo_id": eff_repo, "layer": layer})
        return {
            "generation_code": res.get("generation_id"),
            "job_code": res.get("job_code"),
            "repo_id": eff_repo,
            "note": "生成任务已入队：对话内的实时进度卡正在跟踪五阶段，完成后用例自动进入用例库",
        }

    return run


def _tool_generation_status(repo_id: int, role: str) -> Callable[[dict], dict]:
    """查询生成任务进度与结果：AI 在对话里汇报「生成到哪一步/结果如何」的读侧工具。"""

    def run(args: dict) -> dict:
        from app.models import Cases, Generations

        gen_code = str(args.get("gen_code") or "").strip()
        with get_session() as sess:
            g = sess.query(Generations).filter(Generations.code == gen_code).first() if gen_code else (
                sess.query(Generations).order_by(Generations.id.desc()).first()
            )
            if g is None:
                raise ValueError("找不到生成任务——generate_case 入队后会把 generation_code 告诉你")
            cases = sess.query(Cases).filter(Cases.gen_id == g.code).all()
            runs_ok = [c for c in cases if c.last_run_ok is True]
            return {
                "generation_code": g.code,
                "function": g.target_function,
                "layer": g.layer,
                "status": g.status,
                "cases_total": len(cases),
                "cases_pass": len(runs_ok),
                "case_codes": [c.code for c in cases][:10],
                "note": (
                    "生成完成：用例已入库（可在用例库按该层筛选查看）"
                    if g.status == "done"
                    else f"生成{g.status}——进度详情看「任务队列」或对话里的进度卡"
                ),
            }

    return run


def _tool_upload_knowledge(repo_id: int, role: str) -> Callable[[dict], dict]:
    """用户在对话里贴文档（PRD/接口文档/业务规则）→ 存知识库 + 即时进混合检索。

    生成的六路上下文中 wiki/rag citations 会自动带上这些文档，提升用例质量。
    """

    def run(args: dict) -> dict:
        _require_admin(role)
        from app.core.trace import emit
        from app.models import WikiPages
        from app.services.knowledge.rag import index_documents_bulk

        title = str(args.get("title") or "").strip()
        content = str(args.get("content") or "").strip()
        if not title or not content:
            raise ValueError("title 与 content 必填")
        with get_session() as sess:
            page = WikiPages(
                repo_id=repo_id or None,
                level="user",
                title=title[:200],
                content_md=content,
                rev=1,
                stale=False,
                module="用户上传",
                function="",
            )
            sess.add(page)
            sess.commit()
            wid = page.id
        index_documents_bulk(
            [{"doc_key": f"wiki:{wid}", "kind": "wiki", "repo_id": repo_id, "title": title, "content": content, "meta": {"source": "user-upload", "wiki_id": wid}}]
        )
        emit("仓库", "ai-assistant", f"用户上传知识文档 wiki:{wid} {title[:40]}（已入检索索引）")
        return {"wiki_id": wid, "note": "文档已入库并进入检索索引；后续生成用例的引用上下文会自动引用它"}

    return run


def _tool_create_requirement(repo_id: int, role: str) -> Callable[[dict], dict]:
    def run(args: dict) -> dict:
        _require_admin(role)
        from app.api.requirements import ingest_one

        title = str(args.get("title") or "").strip()
        body = str(args.get("body") or "").strip()
        if not title or not body:
            raise ValueError("title 与 body 必填（body 需包含用户故事与验收条件）")
        res = ingest_one(title, body, repo_id=repo_id, source="ai-assistant")
        return {
            "req_code": res["code"],
            "status": res["status"],
            "testability": res["testability"],
            "note": "需求已录入并完成解析评分；后续可在测试计划中关联该需求",
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
            "name": "changes",
            "description": "变更影响检测：当前仓库 git 工作区未提交的改动映射到已索引函数，返回受影响函数及其调用方与风险分。回答『我这次改了什么、会影响谁』。",
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {"type": "string", "enum": ["unstaged", "staged", "all"], "description": "默认 unstaged（未暂存改动）"},
                },
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
            "name": "trace",
            "description": "调用链追踪：查两个函数之间是否存在调用路径及最短路径（逐跳列出 A→…→B）。回答『A 是怎么一步步调到 B 的』『改了 A 会不会传导到 B』。",
            "parameters": {
                "type": "object",
                "properties": {
                    "src": {"type": "string", "description": "起点函数名"},
                    "dst": {"type": "string", "description": "终点函数名"},
                },
                "required": ["src", "dst"],
            },
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
    {
        "type": "function",
        "function": {
            "name": "generate_case",
            "description": "【写操作·仅管理员】触发生成测试用例（四层任选，入队异步执行，走真实沙箱验证）。用户要求生成用例时调用。层别语义："
            "ut=单元测试（指定函数，断言函数级行为）；fn=功能测试（指定函数，按业务场景出主流程/边界/异常/权限用例）；"
            "api=接口测试（自动抓取运行中网关 OpenAPI 生成 requests 用例，无需函数名）；e2e=E2E 测试（平台真实服务旅程，无需函数名）。"
            "用户没说层别时先问清是哪一层。",
            "parameters": {
                "type": "object",
                "properties": {
                    "function": {"type": "string", "description": "目标函数名（ut/fn 必填；api/e2e 忽略）"},
                    "layer": {"type": "string", "enum": ["ut", "fn", "api", "e2e"], "description": "用例层别，默认 ut"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generation_status",
            "description": "查询生成任务的进度与结果（用例数/沙箱通过数/状态）。用户问「生成完了吗/结果怎么样」或生成入队后需要回查结果时调用。不传 gen_code 返回最近一个任务。",
            "parameters": {
                "type": "object",
                "properties": {
                    "gen_code": {"type": "string", "description": "生成任务号（GEN-xxx），缺省查最近一个"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_requirement",
            "description": "【写操作·仅管理员】录入需求并触发四步解析管线（可测性评分/规则冲突检测）。用户明确要求录入需求时才调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "body": {"type": "string", "description": "用户故事 + 验收条件"},
                },
                "required": ["title", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "upload_knowledge",
            "description": "【写操作·仅管理员】把用户在对话里提供的文档（PRD 片段、接口文档、业务规则、验收标准）存入知识库并即时进入检索索引——后续生成用例的引用上下文会自动引用它。"
            "高质量用例依赖这些数据：用户要求生成用例但上下文不足时，主动引导用户提供需求/规则文档并调用本工具入库。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "文档标题"},
                    "content": {"type": "string", "description": "文档正文（markdown）"},
                },
                "required": ["title", "content"],
            },
        },
    },
]


def _tools_for(repo_id: int, role: str = "admin") -> tuple[dict[str, Callable[[dict], dict]], list[dict]]:
    runners = {
        "search": _tool_search(repo_id),
        "explore": _tool_explore(repo_id),
        "read": _tool_read(repo_id),
        "impact": _tool_impact(repo_id),
        "trace": _tool_trace(repo_id),
        "changes": _tool_changes(repo_id),
        "overview": _tool_overview(repo_id),
        "cases": _tool_cases(repo_id),
        "generate_case": _tool_generate_case(repo_id, role),
        "generation_status": _tool_generation_status(repo_id, role),
        "create_requirement": _tool_create_requirement(repo_id, role),
        "upload_knowledge": _tool_upload_knowledge(repo_id, role),
    }
    return runners, json.loads(json.dumps(TOOL_SPECS))


# ---------------- System Prompt（GitNexus grounding 协议中文翻版） ----------------

SYSTEM_PROMPT = """你是 TestForge 平台的主操作智能体：平台用四层用例生成（单元测试 ut / 功能测试 fn / 接口测试 api / E2E 测试 e2e），
你是这四层生成的对话式入口——用户说要生成用例，由你解析层别、确认目标、调用 generate_case 执行并汇报任务号。
你同时拥有平台知识库与仓库源码的完整访问能力。回答必须有据可查。

## 强制引用（Grounding）
每个事实性结论都必须带引用，格式：`[[文件路径:起-止行]]`（如 [[services/shared/db.py:45-60]]）或 `[[Function:函数名]]`。
- 没有证据就明说「未检索到相关证据」，禁止编造。
- 引用必须是工具结果里真实出现过的路径/行号/函数名。

## 流程可视化
解释调用链、执行流、业务流程时，用 mermaid flowchart 展示（对话界面会渲染成图）：

```mermaid
flowchart LR
    A[入口函数] --> B[传递函数] --> C[(出口: DB/HTTP)]
```

节点名用真实函数名，方向 LR，不要虚构链路——只画工具结果里出现过的调用关系。

## 调查循环
你是调查者，不是一次性问答机：
1. 规划——先说一句要查什么、为什么。
2. 执行——调用工具收集证据。
3. 追溯——search 命中后用 read 验证源码；涉及关系用 explore；评估改动风险用 impact。
4. 交叉验证——结论前用第二个工具确认，不要只凭文档摘要。
5. 落引用——每个发现都标注 [[路径:行]] 或 [[Function:名]]。

每次工具调用前用一句话说明意图。工具失败时修正参数重试，不要停在报错上。

## 用例生成（核心职责）
用户要求生成用例时：
1. 明确层别（ut/fn/api/e2e）——用户没说就问一句，并用一句话解释该层的适用场景。
2. ut/fn 需要目标函数：不确定函数名时先 search/cases 确认，避免对不存在的函数入队。
3. 调 generate_case 入队后，报告任务号。**对话界面会自动出现实时进度卡**（PLAN→守卫→生成→沙箱→回填五阶段），
   你只需说明「进度卡在下方实时更新，完成后我会汇报结果」；用户追问结果时用 generation_status 查询并汇报用例数与沙箱通过数。
4. api/e2e 无需函数名，直接入队即可。

## 高质量用例的数据收集（主动做）
用例质量取决于上下文数据。生成前评估：该目标的业务规则、验收条件、接口约定是否充分？
- 不充分时，主动向用户要：需求片段 / 验收标准 / 业务规则 / 接口约定。
- 用户把文档贴进对话后，调用 upload_knowledge 存入知识库（即时进入检索索引，后续生成自动引用）。
- 有需求可关联时建议先 create_requirement 录入（生成用例会带上 source_req 溯源）。
- 不要为了收集而拖延生成：证据足够就直接干，缺什么补什么。

## 工具策略
- 发现类问题（是什么/有哪些）→ search / overview
- 关系类问题（谁调用/影响谁）→ explore / impact
- 结论前验证 → read 或 read_file（必用，别凭名字猜行为）
- 按文件名模式找文件 / 按内容搜源码 → glob / grep（仅绑定仓库时可用）
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


# ---------------- Agent 循环（deepagents/langgraph 深度代理 + SSE） ----------------


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

    from app.core.config import get_settings

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


def _to_lc_tool(spec: dict, runner: Callable[[dict], dict]):
    """把 TOOL_SPECS 里的 JSON-schema 工具声明 + dict runner 包装成 LangChain StructuredTool。"""
    from pydantic import create_model

    fn = spec["function"]
    fields: dict = {}
    for key, prop in fn.get("parameters", {}).get("properties", {}).items():
        t = prop.get("type", "string")
        py: object = {"string": str, "integer": int, "boolean": bool, "number": float}.get(t, str)
        if "enum" in prop:
            from typing import Literal as _Literal

            py = _Literal[tuple(prop["enum"])]  # type: ignore[valid-type]
        required = key in fn.get("parameters", {}).get("required", [])
        fields[key] = (py, ... if required else None)
    args_model = create_model(f"{fn['name']}_args", **fields)

    def _run(**kwargs) -> dict:  # noqa: ANN003
        try:
            return runner(kwargs)
        except Exception as exc:  # noqa: BLE001 —— 工具错误以结果回填，模型可自修正参数重试
            return {"error": f"工具执行失败: {exc}"}

    _run.__doc__ = fn.get("description", "")
    from langchain_core.tools import StructuredTool

    return StructuredTool.from_function(func=_run, name=fn["name"], description=fn.get("description", ""), args_schema=args_model)


def _repo_fs_root(repo_id: int):
    """仓库检出目录（存在时挂 deepagents 文件工具，锁定该根目录内）。"""
    if not repo_id:
        return None
    with get_session() as sess:
        repo = sess.get(Repos, repo_id)
    if repo is None or not repo.local_path:
        return None
    p = Path(repo.local_path).resolve()
    return str(p) if p.exists() else None


def _build_agent(thread_id: int, repo_id: int, role: str):
    """构建 deepagents 深度代理：领域工具 + 执行沙箱（thread-scoped）+ 仓库只读挂载 + 记忆/摘要 + 调查协议。"""
    from deepagents import create_deep_agent
    from deepagents.middleware import MemoryMiddleware, SummarizationMiddleware

    runners, specs = _tools_for(repo_id, role)
    lc_tools = [_to_lc_tool(spec, runners[spec["function"]["name"]]) for spec in specs]

    from app.services.assistant_sandbox import memory_backend, routed_backend

    fs_root = _repo_fs_root(repo_id)
    backend = routed_backend(thread_id, fs_root)
    llm = _llm_model()
    scope = (
        f"（当前对话绑定仓库 repo_id={repo_id}：/repo/ 前缀下是只读的仓库源码，工作区可写可执行）"
        if repo_id
        else "（未绑定仓库，检索工具将跨全库；工作区可写可执行）"
    )
    return create_deep_agent(
        model=llm,
        tools=lc_tools,
        system_prompt=SYSTEM_PROMPT + f"\n\n当前范围：{scope}",
        backend=backend,
        middleware=[
            # 跨会话记忆：/memory/memory.md 注入系统提示，模型可 edit_file 更新（项目约定/用户偏好越用越懂）
            MemoryMiddleware(backend=memory_backend(), sources=["memory.md"]),  # type: ignore[list-item]  # deepagents 泛型标注摩擦
            # 长对话保护：接近上下文上限时自动摘要压缩（深调查 18+ 工具轮次不爆上下文）
            SummarizationMiddleware(model=llm, backend=backend),
        ],
    ), runners


async def _agent_stream(thread: ChatThread, content: str, role: str = "admin") -> AsyncGenerator[str, None]:
    """deepagents/langgraph 深度代理流式输出。事件：tool_start / tool_end / token / done / error。"""
    from langchain_core.messages import AIMessage, HumanMessage

    repo_id = thread.repo_id or 0
    try:
        with get_session() as sess:
            if repo_id and sess.get(Repos, repo_id) is None:
                repo_id = 0
        agent, runners = _build_agent(thread.id, repo_id, role)

        messages: list = []
        for hist_role, hist in _history(thread.id):
            messages.append(HumanMessage(content=hist) if hist_role == "user" else AIMessage(content=hist))
        messages.append(HumanMessage(content=content))

        pending: dict[str, dict] = {}  # tool_call_id -> {name, args, t0}
        tool_events: list[dict] = []
        final_text = ""

        stream = agent.astream({"messages": messages}, stream_mode=["messages", "updates"], subgraphs=False)
        async for mode, payload in stream:
            if mode == "messages":
                msg = payload[0] if isinstance(payload, tuple) else payload
                if isinstance(msg, AIMessage) or type(msg).__name__ in ("AIMessageChunk", "AIMessage"):
                    text = msg.content if isinstance(msg.content, str) else "".join(str(x) for x in (msg.content or []))
                    if text:
                        final_text += text
                        yield _sse("token", {"text": text})
            elif mode == "updates":
                for _node, upd in (payload or {}).items():
                    if not isinstance(upd, dict):
                        continue
                    for msg in upd.get("messages", []) or []:
                        calls = getattr(msg, "tool_calls", None) or []
                        for call in calls:
                            cid = call.get("id", "")
                            pending[cid] = {"name": call.get("name", ""), "args": call.get("args") or {}, "t0": time.time()}
                            yield _sse("tool_start", {"name": call.get("name", ""), "args": call.get("args") or {}})
                        if type(msg).__name__ == "ToolMessage":
                            cid = getattr(msg, "tool_call_id", "")
                            info = pending.pop(cid, {"name": getattr(msg, "name", ""), "args": {}, "t0": time.time()})
                            raw = getattr(msg, "content", "")
                            text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
                            ok_flag = getattr(msg, "status", "success") != "error"
                            summary = text[:150] + ("…" if len(text) > 150 else "")
                            if not ok_flag:
                                summary = f"工具执行失败: {summary}"
                            ms = int((time.time() - info["t0"]) * 1000)
                            # 结构化结果随事件下发（前端生成进度卡等消费）；解析失败置 None 不影响对话
                            parsed = None
                            if ok_flag:
                                try:
                                    if isinstance(raw, str) and raw.strip().startswith("{"):
                                        parsed = json.loads(raw)
                                    elif isinstance(raw, dict):
                                        parsed = raw
                                except Exception:  # noqa: BLE001
                                    parsed = None
                            tool_events.append({"name": info["name"], "args": info["args"], "summary": summary, "ms": ms, "result": parsed})
                            yield _sse("tool_end", {"name": info["name"], "summary": summary, "ms": ms, "result": parsed})

        if not final_text:
            final_text = "（模型未产出回答，请重试或换个问法。）"

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
async def assistant_thread_create(data: AssistantThreadCreateIn):
    body = data.model_dump()
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
        if sess.get(ChatThread, thread_id) is None:
            raise ApiError(404, "会话不存在", 404)
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
async def assistant_chat(request: Request, data: AssistantChatIn):
    """发消息并流式接收回答（SSE）。body {thread_id, content}。写操作按请求方角色授权。"""
    body = data.model_dump()
    thread_id = int(body.get("thread_id") or 0)
    content = str(body.get("content") or "").strip()
    if not thread_id or not content:
        raise ApiError(400, "thread_id 与 content 必填", 400)
    user = getattr(request.state, "user", {}) or {}
    role = str(user.get("role") or "viewer")
    with get_session() as sess:
        thread = sess.get(ChatThread, thread_id)
        if thread is None:
            raise ApiError(404, "会话不存在", 404)
        sess.add(ChatMessage(thread_id=thread.id, role="user", content=content))
        if thread.title == "新对话":
            thread.title = content[:40]
        sess.commit()

    async def gen():
        async for chunk in _agent_stream(thread, content, role=role):
            yield chunk

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
