"""TestForge MCP Server（GitNexus 思路：平台能力工具化，AI IDE/智能体直接调用）。

stdio 传输 + 换行分隔 JSON-RPC 2.0（MCP 规范子集）：initialize / tools/list / tools/call / ping。
零外部依赖（不引 mcp SDK），协议面足够 Cursor/Claude Code/Codex 等客户端接入。

只读工具面（生成/回归等写操作仍走 REST 网关，鉴权与审计不旁路）：
- list_functions      函数清单/混合检索（repo/module/语义查询）
- function_impact     变更 blast radius（调用深度+置信度）
- search_cases        用例库混合检索（向量+全文 RRF）
- get_symbol_context  符号 360° 上下文包（生成上下文同源，带 citations）
- knowledge_query     文档知识图谱双层检索（local/global/mix）
- repo_status         仓库/知识摄入健康总览

启动：`make mcp` 或 `uv run python -m services.mcp_server`
客户端配置示例（Claude Code / Cursor）：
{
  "mcpServers": {
    "testforge": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/TestForge", "python", "-m", "services.mcp_server"]
    }
  }
}
"""

from __future__ import annotations

import json
import sys
import traceback

PROTOCOL_VERSION = "2024-11-05"


def _tool_defs() -> list[dict]:
    return [
        {
            "name": "list_functions",
            "description": "列出/检索被测仓库的已索引函数（支持 module 前缀与语义混合检索）",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo_id": {"type": "integer", "description": "仓库 id（0=全部）"},
                    "module": {"type": "string", "description": "模块前缀过滤"},
                    "query": {"type": "string", "description": "语义/关键词查询（混合检索）"},
                    "limit": {"type": "integer", "description": "返回上限，默认 20"},
                },
            },
        },
        {
            "name": "function_impact",
            "description": "变更影响面分析（blast radius）：该函数变更会波及哪些调用方，带深度与置信度",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "function": {"type": "string", "description": "函数全名"},
                    "repo_id": {"type": "integer"},
                    "depth": {"type": "integer", "description": "传播深度上限，默认取配置"},
                },
                "required": ["function"],
            },
        },
        {
            "name": "trace_path",
            "description": "调用链追踪：查两个函数之间的最短调用路径（caller→callee BFS 逐跳返回），回答『A 是怎么一步步调到 B 的』",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "src": {"type": "string", "description": "起点函数名"},
                    "dst": {"type": "string", "description": "终点函数名"},
                    "repo_id": {"type": "integer"},
                    "max_depth": {"type": "integer", "description": "路径跳数上限，默认 10"},
                },
                "required": ["src", "dst"],
            },
        },
        {
            "name": "detect_changes",
            "description": "变更影响检测：git diff（未暂存/已暂存/对比基线）→ 受影响函数清单（带直接调用方与风险分）",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo_id": {"type": "integer"},
                    "scope": {"type": "string", "enum": ["unstaged", "staged", "all"], "description": "默认 unstaged"},
                    "base_ref": {"type": "string", "description": "对比基线（分支/tag/commit），提供时优先"},
                },
            },
        },
        {
            "name": "call_cycles",
            "description": "调用环检测：循环依赖强连通分量清单（Tarjan SCC）",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo_id": {"type": "integer"},
                    "max_cycles": {"type": "integer"},
                },
            },
        },
        {
            "name": "entry_chains",
            "description": "执行链路：从入口函数（无调用方）出发的最长调用链，理解代码主干流程",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo_id": {"type": "integer"},
                    "max_chains": {"type": "integer"},
                    "max_len": {"type": "integer"},
                },
            },
        },
        {
            "name": "list_processes",
            "description": "执行流识别：入口→传递→出口（DB/缓存/HTTP）的业务执行流列表，供集成测试/E2E 设计业务旅程参考",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo_id": {"type": "integer"},
                    "max_processes": {"type": "integer"},
                },
            },
        },
        {
            "name": "wiki_ask",
            "description": "Wiki 知识库问答：检索该仓库 Wiki 页后由 LLM 依据资料作答，带来源页；资料不足会明说",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "repo_id": {"type": "integer"},
                },
                "required": ["question"],
            },
        },
        {
            "name": "wiki_lint",
            "description": "Wiki 健康体检：stale/超短页/重复标题/孤儿页/模块覆盖缺口（确定性规则）",
            "inputSchema": {
                "type": "object",
                "properties": {"repo_id": {"type": "integer"}},
                "required": ["repo_id"],
            },
        },
        {
            "name": "search_cases",
            "description": "用例库混合检索（向量+全文 RRF 融合），返回相似用例及元数据",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "get_symbol_context",
            "description": "符号 360° 上下文包：函数源码+依赖+调用方+契约+wiki+知识图谱+相似用例+历史缺陷（与生成管线同源，带引用溯源）",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "function": {"type": "string"},
                    "repo_id": {"type": "integer"},
                },
                "required": ["function"],
            },
        },
        {
            "name": "knowledge_query",
            "description": "文档知识图谱检索（LightRAG 六模式）：naive（原文）/ local（实体级）/ global（主题级）/ hybrid / mix 融合（默认）",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "mode": {"type": "string", "enum": ["naive", "local", "global", "hybrid", "mix"]},
                    "repo_id": {"type": "integer"},
                    "workspace": {"type": "string", "description": "LightRAG 工作区（文档管线摄入的文档所在区）"},
                    "top_k": {"type": "integer", "description": "每路召回条数（默认 8）"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "repo_status",
            "description": "仓库接入状态 + 知识摄入健康（wiki/kg/索引文档状态聚合）+ LLM 缓存台账",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]


def _call_tool(name: str, args: dict) -> str:
    if name == "list_functions":
        from services.shared.db import get_session
        from services.shared.models import Functions
        from services.shared.rag import hybrid_search

        limit = min(int(args.get("limit") or 20), 100)
        query = (args.get("query") or "").strip()
        if query:
            hits = hybrid_search(query, kinds=("function",), limit=limit, repo_id=int(args.get("repo_id") or 0))
            return json.dumps(
                [{"name": h["title"], "module": (h.get("meta") or {}).get("module", ""), "file": (h.get("meta") or {}).get("file", ""), "score": h["score"]} for h in hits],
                ensure_ascii=False,
            )
        with get_session() as sess:
            q = sess.query(Functions)
            if args.get("repo_id"):
                q = q.filter(Functions.repo_id == int(args["repo_id"]))
            if args.get("module"):
                q = q.filter(Functions.module.contains(args["module"]))
            rows = q.order_by(Functions.id).limit(limit).all()
            return json.dumps(
                [{"name": f.name, "module": f.module, "signature": f.signature, "file": f"{f.file}:{f.line}", "language": f.language or "python"} for f in rows],
                ensure_ascii=False,
            )

    if name == "function_impact":
        from services.repo_svc.impact import impact_of
        from services.shared.db import get_session
        from services.shared.models import Functions

        repo_id = int(args.get("repo_id") or 0)
        if not repo_id:
            with get_session() as sess:
                row = sess.query(Functions).filter(Functions.name == args["function"]).first()
                repo_id = row.repo_id if row else 0
        if not repo_id:
            return json.dumps({"error": "function 未索引（repo_id 未知）"}, ensure_ascii=False)
        return json.dumps(
            {"function": args["function"], "repo_id": repo_id, "affected": impact_of(repo_id, args["function"], int(args.get("depth") or 0))},
            ensure_ascii=False,
        )

    if name == "trace_path":
        from services.repo_svc.impact import trace_path
        from services.shared.db import get_session
        from services.shared.models import Functions

        repo_id = int(args.get("repo_id") or 0)
        if not repo_id:
            with get_session() as sess:
                row = sess.query(Functions).filter(Functions.name == args["src"]).first()
                repo_id = row.repo_id if row else 0
        if not repo_id:
            return json.dumps({"error": "起点函数未索引（repo_id 未知）"}, ensure_ascii=False)
        res = trace_path(repo_id, args["src"], args["dst"], int(args.get("max_depth") or 10))
        if res is None:
            return json.dumps({"error": "起点或终点函数未索引"}, ensure_ascii=False)
        return json.dumps(res, ensure_ascii=False)

    if name == "detect_changes":
        from services.repo_svc.changes import detect_changes

        try:
            return json.dumps(
                detect_changes(int(args.get("repo_id") or 0), scope=args.get("scope") or "unstaged", base_ref=args.get("base_ref") or ""),
                ensure_ascii=False,
            )
        except FileNotFoundError as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        except LookupError as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)

    if name == "call_cycles":
        from services.repo_svc.graph_analysis import call_cycles

        return json.dumps(call_cycles(int(args.get("repo_id") or 0), int(args.get("max_cycles") or 20)), ensure_ascii=False)

    if name == "entry_chains":
        from services.repo_svc.graph_analysis import entry_chains

        return json.dumps(
            entry_chains(int(args.get("repo_id") or 0), int(args.get("max_chains") or 10), int(args.get("max_len") or 14)),
            ensure_ascii=False,
        )

    if name == "list_processes":
        from services.repo_svc.graph_analysis import processes

        return json.dumps(
            processes(int(args.get("repo_id") or 0), max_processes=int(args.get("max_processes") or 8)),
            ensure_ascii=False,
        )

    if name == "wiki_ask":
        from services.wiki_builder.ask import ask_wiki

        return json.dumps(ask_wiki(int(args.get("repo_id") or 0), args["question"]), ensure_ascii=False)

    if name == "wiki_lint":
        from services.shared.db import get_session as _gs
        from services.shared.models import WikiPages as _Wp
        from services.wiki_builder.analytics import lint_repo, related_map

        rid = int(args.get("repo_id") or 0)
        with _gs() as s:
            rows = s.query(_Wp).filter(_Wp.repo_id == rid).all()
        related = related_map([{"id": w.id, "title": w.title, "text": f"{w.title}\n{(w.content_md or '')[:2000]}"} for w in rows])
        return json.dumps(
            lint_repo(
                [{"id": w.id, "title": w.title, "level": w.level, "module": w.module, "stale": w.stale, "len": len(w.content_md or "")} for w in rows],
                [],
                related,
            ),
            ensure_ascii=False,
        )

    if name == "search_cases":
        from services.shared.rag import similar_cases

        return json.dumps(
            similar_cases(args["query"], limit=min(int(args.get("limit") or 5), 20)),
            ensure_ascii=False,
        )

    if name == "get_symbol_context":
        from services.testgen_svc.context import build_context

        ctx = build_context(int(args.get("repo_id") or 0), args["function"])
        return json.dumps(
            {
                "function": args["function"],
                "context_sources": [k for k, v in ctx._values().items() if v],
                "rendered": ctx.render(),
                "citations": ctx.citation_dicts(),
            },
            ensure_ascii=False,
        )

    if name == "knowledge_query":
        workspace = str(args.get("workspace") or "")
        if workspace or (args.get("mode") in ("naive", "hybrid")):
            # v2 引擎：工作区文档 / naive / hybrid 模式走六模式引擎
            from services.shared.kg_query import kg_search

            res = kg_search(
                args["query"],
                mode=args.get("mode") or "mix",
                repo_id=int(args.get("repo_id") or 0),
                workspace=workspace,
                top_k=min(int(args.get("top_k") or 8), 30),
            )
            return json.dumps({k: v for k, v in res.items() if k not in ("contexts", "timings")}, ensure_ascii=False)
        from services.shared.docgraph import kg_query

        res = kg_query(int(args.get("repo_id") or 0), args["query"], mode=args.get("mode") or "mix")
        res.pop("rendered", None)
        return json.dumps(res, ensure_ascii=False)

    if name == "repo_status":
        from sqlalchemy import func

        from services.shared.db import get_session
        from services.shared.docgraph import kg_stats
        from services.shared.docstatus import summary as docstatus_summary
        from services.shared.llm_cache import cache_stats
        from services.shared.models import Repos, WikiPages

        with get_session() as sess:
            repos = [
                {"id": r.id, "url": r.url, "branch": r.branch, "status": r.status, "head_rev": r.head_rev[:8]}
                for r in sess.query(Repos).order_by(Repos.id.desc()).limit(20).all()
            ]
            wiki = {str(rid): int(cnt) for rid, cnt in sess.query(WikiPages.repo_id, func.count()).group_by(WikiPages.repo_id).all()}
        return json.dumps(
            {"repos": repos, "wiki_pages": wiki, "kg": kg_stats(), "ingest": docstatus_summary(), "llm_cache": cache_stats()},
            ensure_ascii=False,
        )

    return json.dumps({"error": f"unknown tool: {name}"}, ensure_ascii=False)


def _reply(msg_id, result: dict) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result}, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _error(msg_id, code: int, message: str) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def serve() -> None:
    """主循环：逐行读请求，逐行写响应。stdout 只承载协议，日志一律 stderr。"""
    import logging

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            _error(None, -32700, "parse error")
            continue
        method = req.get("method", "")
        msg_id = req.get("id")
        if method == "initialize":
            _reply(
                msg_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "testforge", "version": "0.2.0"},
                },
            )
        elif method == "ping":
            _reply(msg_id, {})
        elif method == "tools/list":
            _reply(msg_id, {"tools": _tool_defs()})
        elif method == "tools/call":
            params = req.get("params") or {}
            name = params.get("name", "")
            args = params.get("arguments") or {}
            try:
                text = _call_tool(name, args)
                _reply(msg_id, {"content": [{"type": "text", "text": text}], "isError": False})
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc(file=sys.stderr)
                _reply(msg_id, {"content": [{"type": "text", "text": f"tool error: {exc}"}], "isError": True})
        elif method.startswith("notifications/"):
            continue  # 通知不回包
        elif msg_id is not None:
            _error(msg_id, -32601, f"method not found: {method}")


if __name__ == "__main__":
    serve()
