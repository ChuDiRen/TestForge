"""Wiki 问答（OpenWiki wiki_ask 三阶段 RAG 的移植）：

Stage 0 预处理：追问指代解析（"第一个/最后一个" → 上轮来源页）+ 时间过滤
（"本周/最近N天" → 检索窗口，time_filter.rs 移植）。
Stage 1 检索：hybrid_search wiki 单类（向量+全文 RRF，比原版 FTS5+LLM 挑页更强）。
Stage 2 作答：资料组料 → LLM 依据资料回答 → 来源页引用；
引用修补（infer_cited_titles，wiki.rs:617-715 移植）：LLM 忘列来源时按答案中的
标题提及强度（『**标题**』/『来源：』行）反推补充。
source_mode 语义对齐 OpenWiki：knowledge_base=有资料支撑 / no_data=无资料
（no_data 回答禁止存入知识库——反幻觉门卫在服务端 /api/knowledge/documents 强制）。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from app.services.knowledge.llm import chat_once
from app.services.knowledge.rag import hybrid_search

_MAX_PAGES = 6
_PAGE_CHARS = 1500

_SYSTEM = (
    "你是 TestForge 代码知识库的问答助手。只依据【资料】回答问题；"
    "资料不足以回答时，明确说『知识库中没有相关资料』并说明缺什么，不要编造。"
    "回答用中文、简洁、分点；涉及代码行为时给出函数/模块名。"
)

# ---------------- Stage 0a：时间过滤（OpenWiki time_filter.rs 移植） ----------------

_DAYS_WORDS = [
    ("今天", 0), ("今日", 0), ("昨天", 1), ("昨日", 1),
    ("前天", 2), ("本周", 7), ("这一周", 7), ("上周", 14),
    ("本月", 31), ("这个月", 31), ("上月", 62), ("上个月", 62),
]
_RECENT_N = re.compile(r"(?:最近|近|过去)(\d{1,3})\s*(?:天|日)")


def detect_time_range(question: str) -> int | None:
    """问题中的时间词 → 检索窗口天数；无时间语义返回 None（不设限）。"""
    m = _RECENT_N.search(question)
    if m:
        return max(1, min(int(m.group(1)), 365))
    for word, days in _DAYS_WORDS:
        if word in question:
            return max(days, 1)
    return None


def updated_after_from(question: str) -> datetime | None:
    days = detect_time_range(question)
    return datetime.now() - timedelta(days=days) if days is not None else None


# ---------------- Stage 0b：追问指代解析（OpenWiki resolve_follow_up_page_ids 移植） ----------------

_ORDINAL = re.compile(r"第\s*([一二三四五六七八九十\d]+)\s*(?:个|篇|页|条)")
_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def resolve_reference(question: str, last_sources: list[dict]) -> list[dict]:
    """追问指代："第一个/第二个/最后一个/它们" → 直接解析为上一轮来源页。

    返回指代命中的来源页列表（空 = 没有指代语义，走正常检索）。
    """
    if not last_sources:
        return []
    q = question.strip()
    # "它们/这些页/上面那些" → 全部来源
    if re.search(r"它(?:们|)的?(?:内容|详情|细节)|这些(?:页|资料)|上面(?:那些|的页)", q):
        return list(last_sources)
    m = _ORDINAL.search(q)
    if m:
        raw = m.group(1)
        n = int(raw) if raw.isdigit() else _CN_NUM.get(raw, 0)
        if 1 <= n <= len(last_sources):
            return [last_sources[n - 1]]
    if re.search(r"最后(?:一个|一篇|那个|一页)", q):
        return [last_sources[-1]]
    if re.search(r"第一(?:个|篇|页|条)?(?:来源|资料)?", q) and len(q) <= 30:
        return [last_sources[0]]
    return []


def fetch_pages_by_ids(repo_id: int, page_ids: list[int]) -> list[dict]:
    """按 page_id 直取 wiki 页全文（指代命中页不走检索，避免被相似度排序挤掉）。"""
    if not page_ids:
        return []
    from sqlalchemy import text

    from app.db.session import get_session

    with get_session() as sess:
        rows = sess.execute(
            text("SELECT id, title, content_md FROM wiki_pages WHERE id = ANY(:ids) AND repo_id = :rid"),
            {"ids": page_ids, "rid": repo_id or 0},
        ).all()
    return [{"id": r[0], "title": r[1], "content": r[2] or ""} for r in rows]


# ---------------- Stage 2b：引用修补（OpenWiki infer_cited_page_ids_from_titles 移植） ----------------

_MD_BOLD_TITLE = re.compile(r"\*\*([^*]{2,64})\*\*")
_SOURCE_LINE = re.compile(r"^来源[：:]\s*(.+)$", re.MULTILINE)


def answer_mentions_unsourced_titles(answer: str, sources: list[dict]) -> list[str]:
    """答案强提及（**标题** /『来源：』行列出）、但不在检索来源里的标题 →
    供给前端提示『引用了未检索到的页』（原版按标题反推引用页的防漏检思路；
    TestForge 资料即来源，故只做漏检提示，不回填）。"""
    strong: set[str] = set()
    strong.update(t.strip() for t in _MD_BOLD_TITLE.findall(answer))
    for line in _SOURCE_LINE.findall(answer):
        strong.update(t.strip(" 、,，;；") for t in re.split(r"[、,，;；]", line) if t.strip())
    known = {s["title"] for s in sources}
    return [t for t in strong if t and t not in known and len(t) >= 4][:5]


# ---------------- 主流程 ----------------


def ask_wiki(repo_id: int, question: str, history: str = "", last_sources: list[dict] | None = None) -> dict:
    """三阶段问答。last_sources = 上一轮来源页（会话持久化时由网关从上一条 assistant 消息取）。"""
    after = updated_after_from(question)
    last_sources = last_sources or []

    # 指代命中：直取页面全文为主料，仍跑一次检索补背景页
    ref_pages: list[dict] = []
    direct_ids: list[int] = []
    refs = resolve_reference(question, last_sources)
    if refs:
        direct_ids = [int(s["id"]) for s in refs if s.get("id")]
        ref_pages = fetch_pages_by_ids(repo_id, direct_ids)

    hits = hybrid_search(
        question,
        kinds=("wiki",),
        limit=_MAX_PAGES,
        repo_id=repo_id or 0,
        updated_after=after,
    )
    # 指代页并入资料头部且不占检索名额
    seen_ids = {int(h["doc_key"].split(":", 1)[1]) for h in hits if str(h.get("doc_key", "")).startswith("wiki:")}
    merged: list[dict] = []
    for p in ref_pages:
        if p["id"] not in seen_ids:
            merged.append({"doc_key": f"wiki:{p['id']}", "title": p["title"], "content": p["content"], "score": 1.0, "pinned": True})
            seen_ids.add(p["id"])
    merged.extend(hits)

    if not merged:
        return {
            "answer": "知识库中没有检索到相关资料（该仓库可能还未构建 Wiki，或资料不在你指定的时间范围内）。先在『代码库 / Wiki』页执行重建。",
            "sources": [],
            "source_mode": "no_data",
            "time_filter_days": detect_time_range(question),
        }

    blocks = []
    sources = []
    for h in merged:
        page_id = 0
        if str(h.get("doc_key", "")).startswith("wiki:"):
            try:
                page_id = int(str(h["doc_key"]).split(":", 1)[1])
            except ValueError:
                page_id = 0
        pin = "（追问指定页）" if h.get("pinned") else ""
        sources.append({"id": page_id, "title": h.get("title", ""), "score": round(float(h.get("score", 0)), 4)})
        blocks.append(f"【资料 · {h.get('title', '')}】{pin}\n{str(h.get('content', ''))[:_PAGE_CHARS]}")

    q = f"{history}\n{question}".strip() if history else question
    prompt = "【资料】\n" + "\n\n".join(blocks) + f"\n\n【问题】{q}\n\n请依据资料回答；结尾用『来源：』列出引用的资料标题。"
    answer = chat_once(prompt, system=_SYSTEM, role="query")

    # 引用修补：答案强提及但检索漏掉的提示（不篡改 LLM 答案，只补来源元数据）
    unsourced = answer_mentions_unsourced_titles(answer, sources)
    return {
        "answer": answer,
        "sources": [s for s in sources if s["id"]],
        "source_mode": "knowledge_base",
        "unsourced_mentions": unsourced,
        "time_filter_days": detect_time_range(question),
    }
