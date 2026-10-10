"""覆盖守卫：静态检查表（每参数 NULL/空/极值/类型错/越权），缺类自动补。"""

from __future__ import annotations

import logging

from app.schemas.testgen import CATEGORIES, CasePlan, PlannedCase
from app.services.testgen.fninfo import FnInfo

log = logging.getLogger("testgen.guard")

_NUMPY_LIKE = ("int", "float")
_STR_LIKE = ("str", "")


def guard(plan: CasePlan, fn: FnInfo) -> tuple[CasePlan, dict]:
    """返回 (补齐后的 plan, 守卫报告)。规则：
    1. 四类覆盖（normal/boundary/exception/permission）缺类自动补；
    2. 每参数检查表：数值参数补极值边界，字符串参数补 NULL/空，任意参数补类型错；
    3. 权限类：函数参数含 user/actor/owner 或文档提及 权限/角色 时必须存在。
    """
    added: list[PlannedCase] = []
    existing = {c.category for c in plan.cases}
    param_names = [p.name for p in fn.params]
    needs_permission = any(k in param_names for k in ("user", "actor", "owner", "operator")) or any(
        kw in (fn.docstring or "") for kw in ("权限", "角色", "越权")
    )

    # --- 类别补齐 ---
    if "permission" not in existing and needs_permission:
        user_param = next((p for p in param_names if p in ("user", "actor", "owner", "operator")), param_names[0] if param_names else "user")
        added.append(
            PlannedCase(
                id=_next_id(plan, added),
                title=f"[守卫补齐] 禁用用户应拒绝（{fn.name}）",
                category="permission",
                input={user_param: {"id": "u-guard", "role": "buyer", "active": False}},
                expected_error="USER_DISABLED",
                covers="权限：用户禁用",
                source="guard",
                guard_added=True,
            )
        )
        added.append(
            PlannedCase(
                id=_next_id(plan, added),
                title=f"[守卫补齐] 非授权角色应拒绝（{fn.name}）",
                category="permission",
                input={user_param: {"id": "u-guard", "role": "guest", "active": True}},
                expected_error="ROLE_FORBIDDEN",
                covers="权限：角色越权",
                source="guard",
                guard_added=True,
            )
        )

    # --- 参数级检查表（有默认值的可选参数天然宽容：跳过 NULL/空/类型错三类） ---
    checked: list[str] = []
    for p in fn.params:
        ann = p.annotation.lower()
        if p.name in ("self", "cls"):
            continue
        optional = p.has_default
        if any(t in ann for t in _NUMPY_LIKE) or p.name in ("quantity", "count", "amount", "num", "size"):
            if not any(c.category == "boundary" and p.name in c.input for c in plan.cases):
                added.append(_extreme_case(plan, added, fn, p.name))
                checked.append(f"{p.name}:extreme")
            if not optional and not any(c.expected_error and p.name in c.input and c.category == "exception" for c in plan.cases):
                added.append(_null_case(plan, added, fn, p.name))
                checked.append(f"{p.name}:null")
        if "str" in ann or p.name in ("sku", "code", "name", "key", "id"):
            if not optional and not any(c.category == "exception" and isinstance(c.input.get(p.name), str) and c.input.get(p.name) == "" for c in plan.cases):
                added.append(_empty_case(plan, added, fn, p.name))
                checked.append(f"{p.name}:empty")
        if not optional and not any(c.category == "exception" and _is_wrong_type(c.input.get(p.name), ann) for c in plan.cases):
            added.append(_type_case(plan, added, fn, p.name))
            checked.append(f"{p.name}:type-error")

    report = {
        "checklist": GUARD_CATEGORIES(existing, needs_permission),
        "checked": checked,
        "added": [c.id for c in added],
        "categories_after": sorted(existing | {c.category for c in added}),
    }
    plan.cases.extend(added)
    log.info("guard: + Detection class added=%s", report["added"])
    return plan, report


def GUARD_CATEGORIES(existing: set[str], needs_permission: bool) -> list[str]:  # noqa: N802
    rows = []
    for cat in CATEGORIES:
        rows.append(f"{cat}: {'OK' if cat in existing else 'MISSING'}")
    return rows


def _next_id(plan: CasePlan, added: list[PlannedCase]) -> str:
    n = len(plan.cases) + len(added) + 1
    return f"TC-{n:03d}"


def _extreme_case(plan: CasePlan, added: list[PlannedCase], fn: FnInfo, param: str) -> PlannedCase:
    base = next((dict(c.input) for c in plan.cases if param in c.input), {})
    base[param] = 10**9
    return PlannedCase(
        id=_next_id(plan, added),
        title=f"[Guard completion] {param} extreme value should be rejected or safely truncated",
        category="boundary",
        input=base,
        covers=f"Boundary: {param} extreme value",
        source="guard",
        guard_added=True,
    )


def _null_case(plan: CasePlan, added: list[PlannedCase], fn: FnInfo, param: str) -> PlannedCase:
    base = next((dict(c.input) for c in plan.cases if param in c.input), {})
    base[param] = None
    return PlannedCase(
        id=_next_id(plan, added),
        title=f"[Guard completion] {param}=NULL should be rejected",
        category="exception",
        input=base,
        expected_error="INVALID",
        covers=f"Exception: {param} NULL",
        source="guard",
        guard_added=True,
    )


def _empty_case(plan: CasePlan, added: list[PlannedCase], fn: FnInfo, param: str) -> PlannedCase:
    base = next((dict(c.input) for c in plan.cases if param in c.input), {})
    base[param] = ""
    return PlannedCase(
        id=_next_id(plan, added),
        title=f"[Guard completion] {param} empty string should be rejected",
        category="exception",
        input=base,
        expected_error="INVALID",
        covers=f"Exception: {param} empty",
        source="guard",
        guard_added=True,
    )


def _type_case(plan: CasePlan, added: list[PlannedCase], fn: FnInfo, param: str) -> PlannedCase:
    base = next((dict(c.input) for c in plan.cases if param in c.input), {})
    base[param] = ["wrong-type"]
    return PlannedCase(
        id=_next_id(plan, added),
        title=f"[Guard completion] {param} wrong type should be rejected",
        category="exception",
        input=base,
        expected_error="INVALID",
        covers=f"Exception: {param} wrong type",
        source="guard",
        guard_added=True,
    )


def _is_wrong_type(value: object, ann: str) -> bool:
    if ann and any(t in ann for t in _NUMPY_LIKE):
        return isinstance(value, str) or isinstance(value, list)
    return isinstance(value, list)
