"""六路上下文组装：优先级写死 code > contract > wiki > trace > similar > bugs。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from services.shared.config import GRPC_PORTS
from services.shared.db import get_session
from services.shared.grpc_client import grpc_call
from services.shared.models import CallEdges, Cases, Contracts, Defects, Functions, WikiPages

log = logging.getLogger("testgen.ctx")


@dataclass
class ContextBundle:
    code: str = ""  # 源码（ground truth）
    contract: str = ""
    wiki: str = ""
    trace: str = ""
    similar: str = ""
    bugs: str = ""
    sources: list[str] = field(default_factory=list)

    def render(self) -> str:
        """按优先级渲染注入块（冲突以 code/contract 为准）。"""
        blocks = [
            ("code (GROUND TRUTH)", self.code),
            ("contract (GROUND TRUTH)", self.contract),
            ("wiki", self.wiki),
            ("trace", self.trace),
            ("similar (few-shot)", self.similar),
            ("bugs", self.bugs),
        ]
        out = []
        for title, content in blocks:
            if content:
                out.append(f"===== {title} =====\n{content}")
        return "\n\n".join(out)


def build_context(repo_id: int, function: str, module: str = "") -> ContextBundle:
    ctx = ContextBundle()
    # 1. code：函数源码 + 直接依赖签名
    with get_session() as sess:
        q = sess.query(Functions).filter(Functions.name == function)
        if repo_id:
            q = q.filter(Functions.repo_id == repo_id)
        fn = q.order_by(Functions.id.desc()).first()
        if fn is not None:
            ctx.code = fn.source
            fn_module = fn.module
            dep_ids = [e.callee_id for e in sess.query(CallEdges).filter(CallEdges.caller_id == fn.id).all()]
            if dep_ids:
                deps = sess.query(Functions).filter(Functions.id.in_(dep_ids)).all()
                ctx.code += "\n\n# --- 直接依赖签名 ---\n" + "\n".join(f"{d.name}{d.signature}" for d in deps)
        else:
            fn_module = module

        # 2. contract：按模块名匹配契约
        try:
            contracts = sess.query(Contracts).all()
            matched = [c for c in contracts if fn_module and fn_module.split(".")[0] in (c.provider_repo + c.name).lower()]
            if matched:
                ctx.contract = matched[0].spec[:2000]
        except Exception as exc:  # noqa: BLE001
            log.debug("contract ctx skip: %s", exc)

        # 3. wiki：模块页 / 函数卡片
        try:
            pages = sess.query(WikiPages).filter(WikiPages.repo_id == repo_id).all()
            rel = [p for p in pages if (function in p.function or fn_module in p.module) and not p.stale]
            if rel:
                ctx.wiki = "\n\n".join(p.content_md[:1500] for p in rel[:3])
        except Exception as exc:  # noqa: BLE001
            log.debug("wiki ctx skip: %s", exc)

        # 4. similar：相似用例 few-shot（同函数/同模块已入库用例）
        try:
            sims = sess.query(Cases).filter(Cases.target_function == function).limit(3).all()
            if not sims and module:
                sims = sess.query(Cases).filter(Cases.module == module).limit(3).all()
            if sims:
                ctx.similar = "\n".join(f"- {s.code} {s.title} [{s.category}]" for s in sims)
        except Exception as exc:  # noqa: BLE001
            log.debug("similar ctx skip: %s", exc)

        # 5. bugs：历史缺陷模式
        try:
            bugs = sess.query(Defects).order_by(Defects.id.desc()).limit(5).all()
            if bugs:
                ctx.bugs = "\n".join(f"- {b.code} {b.title}" for b in bugs)
        except Exception as exc:  # noqa: BLE001
            log.debug("bugs ctx skip: %s", exc)

    # 6. trace：trace-svc 最近采样（演示为摘要）
    try:
        res = grpc_call("trace-svc", GRPC_PORTS["trace-svc"], "TraceLog", "Query", {"limit": 3}, timeout=2.0)
    except Exception:  # noqa: BLE001
        res = None
    if res:
        ctx.trace = "trace-svc 已接入（采样脱敏后注入）"
    ctx.sources = [n for n, v in (("code", ctx.code), ("contract", ctx.contract), ("wiki", ctx.wiki), ("trace", ctx.trace), ("similar", ctx.similar), ("bugs", ctx.bugs)) if v]
    return ctx
