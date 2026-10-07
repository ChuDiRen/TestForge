"""Wiki 问答（OpenWiki wiki_ask 的移植）：

检索（hybrid_search wiki 单类）→ 组装资料 → LLM 依据资料作答 → 带来源页引用。
资料不足时明确说不足，不编造。多轮上下文由调用方拼接进 question。
"""

from __future__ import annotations

from services.shared.llm import chat_once
from services.shared.rag import hybrid_search

_MAX_PAGES = 6
_PAGE_CHARS = 1500

_SYSTEM = (
    "你是 TestForge 代码知识库的问答助手。只依据【资料】回答问题；"
    "资料不足以回答时，明确说『知识库中没有相关资料』并说明缺什么，不要编造。"
    "回答用中文、简洁、分点；涉及代码行为时给出函数/模块名。"
)


def ask_wiki(repo_id: int, question: str, history: str = "") -> dict:
    q = f"{history}\n{question}".strip() if history else question
    hits = hybrid_search(question, kinds=("wiki",), limit=_MAX_PAGES, repo_id=repo_id or 0)
    if not hits:
        return {
            "answer": "知识库中没有检索到相关资料（该仓库可能还未构建 Wiki）。先在『代码库 / Wiki』页执行重建。",
            "sources": [],
        }

    blocks = []
    sources = []
    for h in hits:
        page_id = 0
        if str(h.get("doc_key", "")).startswith("wiki:"):
            try:
                page_id = int(str(h["doc_key"]).split(":", 1)[1])
            except ValueError:
                page_id = 0
        sources.append({"id": page_id, "title": h.get("title", ""), "score": round(float(h.get("score", 0)), 4)})
        blocks.append(f"【资料 · {h.get('title', '')}】\n{str(h.get('content', ''))[:_PAGE_CHARS]}")

    prompt = "【资料】\n" + "\n\n".join(blocks) + f"\n\n【问题】{q}\n\n请依据资料回答；结尾用『来源：』列出引用的资料标题。"
    answer = chat_once(prompt, system=_SYSTEM, role="query")
    return {"answer": answer, "sources": [s for s in sources if s["id"]]}
