"""契约中心（M4）：注册 / 列表 / diff / 影响分析 / 定向重生成 / 变更完整闭环。"""

import json
import logging

from fastapi import Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.crud import crud_contracts
from app.main import ApiError, app, get_session, ok
from app.models import Cases, ContractDiffs, Contracts
from app.schemas.contracts import (
    ContractCreateIn,
    ContractImpactIn,
    ContractRegenerateIn,
    RegenerateAffectedIn,
)

log = logging.getLogger("app.api.contract")


@app.post("/api/contracts/create")
async def register_contract(data: ContractCreateIn):
    body = data.model_dump()
    name = (body.get("name") or "").strip()
    if not name:
        raise ApiError(1001, "name 必填")
    from app.services.contract.api import register as contract_register

    res = contract_register(
        name=name,
        ctype=body.get("type") or "rest",
        provider_repo=body.get("provider_repo") or "",
        version=body.get("version") or "v1.0.0",
        spec=json.dumps(body.get("spec") or {}, ensure_ascii=False),
        consumers=body.get("consumers") or [],
    )
    return ok(res)


@app.get("/api/contracts")
def list_contracts(db: Session = Depends(get_db)):
    rows = db.query(Contracts).order_by(Contracts.id.desc()).limit(200).all()
    out = []
    for c in rows:
        last_diff = db.query(ContractDiffs).filter(ContractDiffs.contract_id == c.id).order_by(ContractDiffs.id.desc()).first()
        out.append(
            {
                "id": c.id,
                "name": c.name,
                "type": c.type,
                "provider_repo": c.provider_repo,
                "version": c.version,
                "consumers": json.loads(c.consumers or "[]"),
                "spec": json.loads(c.spec) if c.spec else {},
                "last_breaking": bool(last_diff.breaking) if last_diff else False,
                "last_diff": json.loads(last_diff.detail).get("changes", []) if last_diff else [],
                "from_v": last_diff.from_v if last_diff else None,
            }
        )
    return ok(out)


@app.post("/api/contracts/{contract_id}/impact")
async def contract_impact(contract_id: int, data: ContractImpactIn | None = None):
    """影响分析：传播链 + 受影响资产清单（wiki 页 stale / 用例打标）。空请求体按 {} 处理。"""
    body = data.model_dump() if data else {}
    to_v = body.get("to_v") or ""
    from app.services.contract.api import impact as contract_impact_events

    res = contract_impact_events(contract_id, to_v=to_v)
    return ok(res)


@app.post("/api/regenerate")
async def regenerate_affected(data: RegenerateAffectedIn):
    """定向重生成受影响用例（非全量）。"""
    body = data.model_dump()
    repo_id = int(body.get("repo_id") or 0)
    target = body.get("target_function") or ""
    if not repo_id or not target:
        raise ApiError(1001, "repo_id 与 target_function 必填")
    from app.services.testgen.api import regenerate_affected

    res = regenerate_affected(
        repo_id=repo_id,
        target_function=target,
        reason=body.get("reason") or "contract-impact",
        source_req=body.get("source_req") or "",
    )
    return ok(res)


@app.post("/api/contracts/{contract_id}/regenerate")
async def contract_regenerate(contract_id: int, data: ContractRegenerateIn | None = None):
    """契约变更完整闭环：影响分析 → 受影响用例的目标函数逐个走「生成+沙箱执行」完整链路。

    每个目标函数入队一个生成任务（含真实执行与失败建缺陷），结果看 /api/jobs 与 runs。
    空请求体按 {} 处理。
    """
    from app.api.generations import new_generation

    body = data.model_dump() if data else {}
    to_v = body.get("to_v") or ""
    from app.services.contract.api import impact as contract_impact_events

    events = contract_impact_events(contract_id, to_v=to_v)
    case_codes = [str(e.get("asset_id")) for e in events if e.get("asset_type") == "case"]
    with get_session() as sess:
        rows = sess.query(Cases).filter(Cases.code.in_(case_codes)).all() if case_codes else []
        targets = sorted({c.target_function for c in rows if c.target_function})
        repo_id = next((c.repo_id or 0 for c in rows if c.repo_id), 0)
    if not targets:
        return ok({"affected_cases": len(case_codes), "targets": [], "generations": [], "note": "受影响用例未解析出可生成目标"})
    gens = [
        new_generation({"function": t, "repo_id": repo_id, "layer": "ut", "source_req": f"contract-{contract_id}"})
        for t in targets
    ]
    return ok({"affected_cases": len(case_codes), "targets": targets, "generations": gens})


@app.get("/api/contracts/{contract_id}/diffs")
def contract_diffs(contract_id: int, db: Session = Depends(get_db)):
    if crud_contracts.get(db, contract_id) is None:
        raise ApiError(404, "契约不存在", 404)
    rows = db.query(ContractDiffs).filter(ContractDiffs.contract_id == contract_id).order_by(ContractDiffs.id.desc()).all()
    return ok(
        [
            {"from_v": d.from_v, "to_v": d.to_v, "breaking": d.breaking, "changes": json.loads(d.detail).get("changes", [])}
            for d in rows
        ]
    )
