"""符号 360° 上下文包：优先级写死 code > contract > wiki > kg > trace > similar > bugs。

GitNexus context 思路：一次组装完整符号视图（函数卡片+依赖+调用方+契约+知识+历史），
带 token 预算——超限按优先级从低到高裁剪，冲突以 code/contract 为准（GROUND TRUTH 只截不清）。
LightRAG 检索融合：similar/wiki/bugs 走混合检索（向量+全文 RRF），kg 走文档图谱双层检索。

citations：每一路的知识来源登记为引用（kind, ref, detail），随生成管线一路带到
用例 schema 与准出报告——生成物逐条可回溯。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.core.config import get_settings
from app.db.session import get_session
from app.models import CallEdges, Cases, Contracts, Defects, Functions, WikiPages

log = logging.getLogger("testgen.ctx")

# 裁剪顺序（低→高）：超预算时先清前面的块；GROUND TRUTH 两块只截断不清空
_TRIM_ORDER = [
    ("bugs", "bugs"),
    ("similar", "similar (few-shot)"),
    ("trace", "trace"),
    ("kg", "kg (knowledge graph)"),
    ("wiki", "wiki"),
]
_GROUND_TRUTH = [("contract", "contract (GROUND TRUTH)"), ("code", "code (GROUND TRUTH)")]


def estimate_tokens(text: str) -> int:
    """粗估 token：CJK 字符≈1 token/字，拉丁≈1 token/4 字符。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk + (len(text) - cjk) // 4


@dataclass
class Citation:
    kind: str  # code|contract|wiki|kg|similar|bugs|trace
    ref: str  # file:line / contract@version / wiki:123 / kg:实体名 / CASE-x / BUG-x
    detail: str = ""

    def as_dict(self) -> dict:
        return {"kind": self.kind, "ref": self.ref, "detail": self.detail}


@dataclass
class ContextBundle:
    code: str = ""  # 源码（ground truth）
    contract: str = ""
    wiki: str = ""
    kg: str = ""  # 文档知识图谱双层检索结果
    trace: str = ""
    similar: str = ""
    bugs: str = ""
    citations: list[Citation] = field(default_factory=list)

    def _values(self) -> dict[str, str]:
        return {
            "code": self.code,
            "contract": self.contract,
            "wiki": self.wiki,
            "kg": self.kg,
            "trace": self.trace,
            "similar": self.similar,
            "bugs": self.bugs,
        }

    def _titles(self) -> dict[str, str]:
        return {
            "code": "code (GROUND TRUTH)",
            "contract": "contract (GROUND TRUTH)",
            "wiki": "wiki",
            "kg": "kg (knowledge graph)",
            "trace": "trace",
            "similar": "similar (few-shot)",
            "bugs": "bugs",
        }

    def total_tokens(self) -> int:
        return sum(estimate_tokens(v) for v in self._values().values())

    @property
    def sources(self) -> list[str]:
        """已命中的上下文路名（按优先级顺序）。"""
        return [k for k in ("code", "contract", "wiki", "kg", "trace", "similar", "bugs") if getattr(self, k)]

    def enforce_budget(self, budget: int | None = None) -> int:
        """超预算按优先级从低到高裁剪（GROUND TRUTH 只截不清）。返回裁剪后 token 数。"""
        budget = budget or get_settings().ctx_token_budget
        for _ in range(32):  # 防死循环硬顶
            if self.total_tokens() <= budget:
                break
            for attr, _title in _TRIM_ORDER:
                val = getattr(self, attr)
                if not val:
                    continue
                setattr(self, attr, "" if attr != "wiki" else val[: len(val) // 2])
                break
            else:
                # 只剩 GROUND TRUTH：code 截尾（保留签名与主干头部）
                if estimate_tokens(self.code) > budget // 2:
                    keep = max(200, budget * 2)
                    cut = self.code.rfind("\n", 0, keep)
                    self.code = self.code[: cut + 1] if cut > 0 else self.code[:keep]
                else:
                    break
        return self.total_tokens()

    def render(self) -> str:
        """按优先级渲染注入块（冲突以 code/contract 为准）。"""
        out = []
        for attr, title in reversed(_TRIM_ORDER + _GROUND_TRUTH):
            content = getattr(self, attr)
            if content:
                out.append(f"===== {title} =====\n{content}")
        return "\n\n".join(out)

    def citation_dicts(self) -> list[dict]:
        seen: set[str] = set()
        out = []
        for c in self.citations:
            if c.ref in seen:
                continue
            seen.add(c.ref)
            out.append(c.as_dict())
        return out


def build_context(repo_id: int, function: str, module: str = "") -> ContextBundle:
    ctx = ContextBundle()
    # 1. code：函数源码 + 直接依赖签名 + 调用方（360° 符号视图）
    with get_session() as sess:
        q = sess.query(Functions).filter(Functions.name == function)
        if repo_id:
            q = q.filter(Functions.repo_id == repo_id)
        fn = q.order_by(Functions.id.desc()).first()
        if fn is not None:
            ctx.code = fn.source
            ctx.citations.append(Citation("code", f"{fn.file}:{fn.line}", f"被测函数 {fn.name}{fn.signature}"))
            fn_module = fn.module
            dep_ids = [e.callee_id for e in sess.query(CallEdges).filter(CallEdges.caller_id == fn.id).all()]
            if dep_ids:
                deps = sess.query(Functions).filter(Functions.id.in_(dep_ids)).all()
                ctx.code += "\n\n# --- 直接依赖签名 ---\n" + "\n".join(f"{d.name}{d.signature}" for d in deps)
                for d in deps[:5]:
                    ctx.citations.append(Citation("code", f"{d.file}:{d.line}", f"直接依赖 {d.name}"))
            caller_ids = [e.caller_id for e in sess.query(CallEdges).filter(CallEdges.callee_id == fn.id).limit(5).all()]
            if caller_ids:
                callers = sess.query(Functions).filter(Functions.id.in_(caller_ids)).all()
                ctx.code += "\n\n# --- 调用方（上游契约约束参考） ---\n" + "\n".join(f"# {c.name} @ {c.file}:{c.line}" for c in callers)
        else:
            fn_module = module

        # 2. contract：按模块名匹配契约
        try:
            contracts = sess.query(Contracts).all()
            matched = [c for c in contracts if fn_module and fn_module.split(".")[0] in (c.provider_repo + c.name).lower()]
            if matched:
                ctx.contract = matched[0].spec[:2000]
                ctx.citations.append(Citation("contract", f"{matched[0].name}@{matched[0].version}", "契约规格（GROUND TRUTH）"))
        except Exception as exc:  # noqa: BLE001
            log.debug("contract ctx skip: %s", exc)

        # 3. wiki：混合检索（向量+全文 RRF），旧全表扫描仅兜底
        # kinds 含 user_doc（用户上传知识）与 lesson（已关闭缺陷沉淀的教训）——
        # 生成同模块用例时自动召回历史教训，同一个坑不踩第二遍
        try:
            from app.services.knowledge.rag import hybrid_search

            hits = hybrid_search(f"{function} {fn_module}", kinds=("wiki", "user_doc", "lesson"), limit=3, repo_id=repo_id or 0)
            fresh = [h for h in hits if not (h.get("meta") or {}).get("stale")]
            if fresh:
                ctx.wiki = "\n\n".join(h["content"][:1500] for h in fresh)
                for h in fresh:
                    dk = h["doc_key"]
                    label = (
                        f"缺陷教训 {h['title']}" if dk.startswith("lesson:")
                        else f"知识文档 {h['title']}" if dk.startswith("userdoc:")
                        else f"wiki 页 {h['title']}"
                    )
                    ctx.citations.append(Citation("wiki", dk, label))
        except Exception as exc:  # noqa: BLE001
            log.debug("wiki ctx skip: %s", exc)
        if not ctx.wiki:
            try:
                pages = sess.query(WikiPages).filter(WikiPages.repo_id == repo_id).all()
                rel = [p for p in pages if (function in p.function or fn_module in p.module) and not p.stale]
                if rel:
                    ctx.wiki = "\n\n".join(p.content_md[:1500] for p in rel[:3])
                    for p in rel[:3]:
                        ctx.citations.append(Citation("wiki", f"wiki:{p.id}", f"wiki 页 {p.title}"))
            except Exception as exc:  # noqa: BLE001
                log.debug("wiki fallback skip: %s", exc)

        # 3b. kg：文档知识图谱双层检索（local+global，Key 缺失时自动降级为确定性检索）
        try:
            from app.services.knowledge.docgraph import kg_query

            kg_res = kg_query(repo_id or 0, f"{function} {fn_module} 测试要点", mode="mix", limit=4)
            if kg_res.get("rendered"):
                ctx.kg = kg_res["rendered"]
                for e in kg_res.get("entities", [])[:3]:
                    ctx.citations.append(Citation("kg", f"kg:{e['name']}", "文档图谱实体"))
        except Exception as exc:  # noqa: BLE001
            log.debug("kg ctx skip: %s", exc)

        # 4. similar：相似用例 few-shot（混合检索优先，目标函数直查兜底）
        try:
            from app.services.knowledge.rag import similar_cases

            sims = similar_cases(function, limit=3)
            if sims:
                ctx.similar = "\n".join(f"- {s['code']} {s['title']} [{s['category']}] sim={s['score']}" for s in sims)
                for s in sims:
                    ctx.citations.append(Citation("similar", s["code"], f"相似用例 sim={s['score']}"))
        except Exception as exc:  # noqa: BLE001
            log.debug("rag similar skip: %s", exc)
        if not ctx.similar:
            try:
                sims = sess.query(Cases).filter(Cases.target_function == function).limit(3).all()
                if sims:
                    ctx.similar = "\n".join(f"- {s.code} {s.title} [{s.category}]" for s in sims)
                    for s in sims:
                        ctx.citations.append(Citation("similar", s.code, "同目标历史用例"))
            except Exception as exc:  # noqa: BLE001
                log.debug("similar ctx skip: %s", exc)

        # 5. bugs：历史缺陷模式（混合检索相关缺陷，退化取最近）
        try:
            from app.services.knowledge.rag import hybrid_search

            bug_hits = hybrid_search(f"{function} {fn_module}", kinds=("defect",), limit=3)
            if bug_hits:
                ctx.bugs = "\n".join(f"- {h['title']}: {h['content'][:150]}" for h in bug_hits)
                for h in bug_hits:
                    ctx.citations.append(Citation("bugs", h["doc_key"], "相关历史缺陷"))
        except Exception as exc:  # noqa: BLE001
            log.debug("bugs rag skip: %s", exc)
        if not ctx.bugs:
            try:
                bugs = sess.query(Defects).order_by(Defects.id.desc()).limit(5).all()
                if bugs:
                    ctx.bugs = "\n".join(f"- {b.code} {b.title}" for b in bugs)
                    for b in bugs[:3]:
                        ctx.citations.append(Citation("bugs", b.code, "近期缺陷"))
            except Exception as exc:  # noqa: BLE001
                log.debug("bugs ctx skip: %s", exc)

    # 6. trace：最近采样（演示为摘要）
    try:
        from app.core.trace import query as trace_query

        res = trace_query(limit=3)
    except Exception:  # noqa: BLE001
        res = []
    if res:
        ctx.trace = "trace-svc 已接入（采样脱敏后注入）"
        ctx.citations.append(Citation("trace", "trace-svc:sample", "运行时 trace 采样"))

    # token 预算：超限按优先级裁剪（GROUND TRUTH 只截不清）
    try:
        ctx.enforce_budget()
    except Exception as exc:  # noqa: BLE001
        log.debug("budget enforce skip: %s", exc)
    return ctx
