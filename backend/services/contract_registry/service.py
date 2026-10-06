"""contract-registry 业务：契约注册、版本 diff、breaking 识别、影响分析。

breaking 规则（确定性）：
- rest: 删除字段 / 新增必填字段 / 删除端点 / 错误码弃用
- grpc: 删除 rpc / 删除字段
- topic: 删除字段 / 改字段类型
"""

from __future__ import annotations

import json
import logging

from services.shared.db import get_session
from services.shared.models import Cases, ContractDiffs, Contracts, Functions, WikiPages
from services.shared.trace import emit

log = logging.getLogger("contract-registry")

PROPAGATE = "传播链：提供方发布 → 消费方 wiki stale → 关联用例打标 → 定向重生成"


def register(name: str, ctype: str, provider_repo: str, version: str, spec: str, consumers: list[str]) -> dict:
    """注册契约；同名已存在则视为新版本，计算 diff + breaking。

    消费方自动匹配（GitNexus group_sync 思路）：扫描全部仓库函数源码，
    命中契约名/端点/rpc 的函数归属消费方，与显式传入 consumers 合并。
    """
    with get_session() as sess:
        old = sess.query(Contracts).filter(Contracts.name == name).order_by(Contracts.id.desc()).first()
        if old is None:
            detected = _detect_consumers(name, spec)
            merged = sorted(set(consumers or []) | set(detected))
            c = Contracts(name=name, type=ctype, provider_repo=provider_repo, version=version, spec=spec, consumers=json.dumps(merged, ensure_ascii=False), status="active")
            sess.add(c)
            sess.commit()
            emit("契约", "contract-registry", f"契约注册 {name} {version}（{ctype}）消费方={merged}")
            return {"contract_id": c.id, "version": version, "breaking": False, "changes": [], "first": True, "consumers": merged}

        changes, breaking = diff_specs(old.spec or "", spec, ctype)
        detected = _detect_consumers(name, spec)
        merged = sorted(set(consumers or []) | set(detected))
        old.version = version
        old.spec = spec
        old.consumers = json.dumps(merged, ensure_ascii=False)
        sess.add(ContractDiffs(contract_id=old.id, from_v=old.version, to_v=version, breaking=breaking, detail=json.dumps({"changes": changes}, ensure_ascii=False)))
        sess.commit()
        emit("契约", "contract-registry", f"契约变更 {name} {old.version}→{version} breaking={breaking} 变更={len(changes)} 消费方={merged}")
        return {"contract_id": old.id, "version": version, "breaking": breaking, "changes": changes, "first": False, "consumers": merged}


def _contract_tokens(name: str, spec: str) -> list[str]:
    """契约可检索词：名称 + rest 端点路径段 / grpc rpc 名 / topic 字段名。"""
    tokens = [name.lower()]
    try:
        obj = json.loads(spec or "{}")
    except json.JSONDecodeError:
        return tokens
    for p in (obj.get("paths") or {}):
        tokens.extend(seg.lower() for seg in str(p).split("/") if len(seg) >= 4)
    tokens.extend(str(r).lower() for r in (obj.get("rpcs") or []))
    tokens.extend(str(f).lower() for f in (obj.get("fields") or {}))
    return [t for t in tokens if len(t) >= 4]


def _detect_consumers(name: str, spec: str) -> list[str]:
    """扫描全部仓库函数源码：命中契约词的函数所在仓库 → 自动登记为消费方。"""
    from services.shared.models import Functions, Repos

    tokens = _contract_tokens(name, spec)
    if not tokens:
        return []
    hits: dict[int, int] = {}
    with get_session() as sess:
        repo_names = {r.id: (r.url.rsplit("/", 1)[-1].removesuffix(".git") or str(r.id)) for r in sess.query(Repos).all()}
        for fid, rid, source in sess.query(Functions.id, Functions.repo_id, Functions.source).yield_per(200):
            src = (source or "").lower()
            if src and any(t in src for t in tokens):
                hits[rid] = hits.get(rid, 0) + 1
    return sorted({repo_names.get(rid, str(rid)) for rid in hits})


def diff_specs(old_spec: str, new_spec: str, ctype: str) -> tuple[list[str], bool]:
    try:
        old = json.loads(old_spec or "{}")
        new = json.loads(new_spec or "{}")
    except json.JSONDecodeError:
        return (["spec 非 JSON，跳过 diff"], False)

    changes: list[str] = []
    breaking = False
    if ctype == "rest":
        old_paths = set((old.get("paths") or {}).keys())
        new_paths = set((new.get("paths") or {}).keys())
        for p in old_paths - new_paths:
            changes.append(f"- 端点删除 {p}")
            breaking = True
        for p in new_paths - old_paths:
            changes.append(f"+ 端点新增 {p}")
        for p in old_paths & new_paths:
            for method in ("post", "get", "put", "delete"):
                props = lambda spec: (((spec.get("paths") or {}).get(p) or {}).get(method) or {}).get("__fields__", {})  # noqa: E731
                of, nf = props(old), props(new)
                for f in of:
                    if f not in nf:
                        changes.append(f"- {method.upper()} {p} 响应/字段移除 {f}")
                        breaking = True
                for f, meta in nf.items():
                    if f not in of and (meta.get("required") if isinstance(meta, dict) else False):
                        changes.append(f"+ {method.upper()} {p} 新增必填 {f}")
                        breaking = True
                    elif f not in of:
                        changes.append(f"+ {method.upper()} {p} 新增可选 {f}")
        old_err = set((old.get("error_codes") or []))
        new_err = set((new.get("error_codes") or []))
        for e in old_err - new_err:
            changes.append(f"- 错误码弃用 {e}")
            breaking = True
    elif ctype == "grpc":
        old_rpc = set((old.get("rpcs") or []))
        new_rpc = set((new.get("rpcs") or []))
        for r in old_rpc - new_rpc:
            changes.append(f"- rpc 删除 {r}")
            breaking = True
        for f in (old.get("fields") or {}):
            if f not in (new.get("fields") or {}):
                changes.append(f"- 消息字段删除 {f}")
                breaking = True
    elif ctype == "topic":
        for f in (old.get("fields") or {}):
            nf = (new.get("fields") or {}).get(f)
            if nf is None:
                changes.append(f"- 事件字段删除 {f}")
                breaking = True
            elif isinstance(nf, dict) and nf.get("type") != (old.get("fields") or {}).get(f, {}).get("type"):
                changes.append(f"~ 事件字段类型变更 {f}")
                breaking = True

    if not changes:
        changes.append("无结构变更")
    else:
        changes.sort()  # 集合迭代序跨进程不确定；diff 审计输出必须确定
    return changes, breaking


DOMAIN_TERMS = {
    "payment": ("pay", "支付", "payment"),
    "order": ("order", "订单"),
    "inventory": ("inventory", "库存"),
}


def impact(contract_id: int, to_v: str, trace_id: str = "") -> list[dict]:
    """影响传播：消费方 wiki stale + 关联用例打标，返回资产清单。

    双通道匹配：消费方源码精确命中（函数引用了契约端点/rpc/字段，跨仓 group_sync）
    + 域词启发式（payment/order 等历史兜底）。
    """
    events: list[dict] = []
    with get_session() as sess:
        c = sess.get(Contracts, contract_id)
        if c is None:
            return events
        domain = _domain_of(c.name)
        terms = DOMAIN_TERMS.get(domain, (domain,))
        tokens = _contract_tokens(c.name, c.spec or "")

        def hit(text: str) -> bool:
            t = (text or "").lower()
            return any(term in t for term in terms)

        consumers = json.loads(c.consumers or "[]")
        # 0) 消费方源码精确匹配：引用契约词的函数 → 直接定位受影响用例
        matched_fns: list[str] = []
        if tokens:
            for fname, source in sess.query(Functions.name, Functions.source).yield_per(200):
                src = (source or "").lower()
                if src and any(t in src for t in tokens):
                    matched_fns.append(fname)
        # 1) 消费方 wiki 页 stale（consumers 或域词匹配模块页）
        for page in sess.query(WikiPages).filter(WikiPages.level == "module").all():
            if any(cons in f"{page.module} {page.title}" for cons in consumers) or hit(page.module) or hit(page.title):
                page.stale = True
                events.append({"asset_type": "wiki_page", "asset_id": str(page.id), "reason": f"契约 {c.name} 变更 → 模块页 {page.module} stale", "stale_wiki": True, "case_tagged": False})
        # 2) 关联用例打标：精确命中目标函数优先，域词兜底
        tagged = False
        for case in sess.query(Cases).filter(Cases.status == "已入库").limit(500).all():
            precise = (case.target_function or "") in matched_fns
            if precise or hit(case.module) or hit(case.title) or hit(case.target_function):
                case.review_note = f"[contract-impact] {c.name}@{to_v}" + (" 精确匹配" if precise else "")
                events.append({"asset_type": "case", "asset_id": case.code, "reason": ("源码引用契约词（精确）" if precise else "断言引用 ") + f"{c.name} 旧契约字段", "stale_wiki": False, "case_tagged": True})
                tagged = True
        sess.commit()
        # 3) 传播链记录
        events.append({"asset_type": "propagation", "asset_id": PROPAGATE, "reason": f"{c.name} {c.version}→{to_v} 消费方={consumers} 精确命中函数={len(matched_fns)}", "stale_wiki": True, "case_tagged": tagged})
    emit("契约", "contract-registry", f"影响分析 {c.name}@{to_v}: 资产 {len(events)} 项 消费方精确命中 {len(matched_fns)} 函数", trace_id=trace_id or None)
    return events


def _domain_of(name: str) -> str:
    n = (name or "").lower()
    for d in ("payment", "pay", "order", "inventory", "user"):
        if d in n:
            return "pay" if d == "payment" else d
    return n.split()[0][:6] if n.split() else n


def regenerate_affected(repo_id: int, case_ids: list[str], target: str, reason: str, source_req: str, trace_id: str) -> dict:
    """定向重生成（仅受影响用例，非全量）。"""
    from services.shared.config import GRPC_PORTS
    from services.shared.grpc_client import grpc_call

    res = grpc_call(
        "testgen-svc",
        GRPC_PORTS["testgen-svc"],
        "TestGen",
        "RegenerateAffected",
        {"repo_id": repo_id, "case_ids": case_ids, "trace_id": trace_id, "reason": reason, "target_function": target, "source_req": source_req},
        timeout=60,
    )
    return res
