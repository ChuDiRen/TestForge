"""需求（M3）：录入解析、冲突确认、自动编排、质量流水线 G0~G5。"""

import json
import logging
from datetime import datetime

from fastapi import Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.trace import emit
from app.main import ApiError, _count_by, app, get_session, ok
from app.models import Cases, Defects, Requirements, Runs, WikiPages
from app.schemas.requirements import RequirementConfirmIn, RequirementIngestIn

log = logging.getLogger("app.api.req")

GATES = ("G0 可测性", "G1 知识就绪", "G2 用例覆盖", "G3 执行验证", "G4 缺陷清零", "G5 准出报告")


def _next_req_code(sess) -> str:  # type: ignore[no-untyped-def]
    n = sess.query(Requirements).count() + 101
    while sess.query(Requirements).filter(Requirements.code == f"REQ-{n}").first() is not None:
        n += 1
    return f"REQ-{n}"


def ingest_one(title: str, text: str, repo_id: int = 0, source: str = "paste") -> dict:
    """需求录入四步管线（建单 → 解析 → 评分定级 → 留痕），路由与 AI 助手写工具共用。"""
    with get_session() as sess:
        code = _next_req_code(sess)
        req = Requirements(code=code, title=title, source=source, body=text, repo_id=repo_id or None, status="解析中", trace_id="")
        sess.add(req)
        sess.commit()
        rid = req.id

    from app.services.req.api import parse as req_parse

    report = req_parse(code=code, title=title, body=text, source=source, repo_id=repo_id)
    score = float(report.get("testability") or 0)
    conflict = bool(report.get("conflict"))
    status = "已打回" if score < 80 else ("规则冲突待确认" if conflict else "待人审")
    profile = {
        "G0": {"ok": score >= 80, "score": score, "ts": datetime.now().isoformat(), "note": "AI 自动判定" if score >= 80 else "自动打回产品"},
    }
    with get_session() as sess:
        req = sess.get(Requirements, rid)
        req.status = status
        req.testability_score = score
        req.parse_report = json.dumps(
            {"story": report.get("story", ""), "rules": report.get("rules", []), "conflict": conflict, "conflict_detail": report.get("conflict_detail", ""), "pipeline": report.get("pipeline", []), "new_rules": report.get("new_rules", [])},
            ensure_ascii=False,
        )
        req.quality_profile = json.dumps(profile, ensure_ascii=False)
        sess.commit()
    emit("需求", "qa-录入" if score >= 80 else "req-svc", f"需求 {code} 解析完成：{status}（可测性 {score:.0f}）", req_code=code)
    # 需求文档入检索库（kind='req'）：全局搜索与生成上下文的语料源（测试资料五类之一）
    try:
        from app.services.knowledge.rag import ensure_rag_documents_table, index_document

        ensure_rag_documents_table()
        index_document(
            f"req:{code}",
            "req",
            f"需求 {code} {title}",
            f"需求 {code}（状态：{status}，可测性 {score:.0f}）\n来源：{source}\n\n{text[:5000]}",
            repo_id=repo_id or 0,
            meta={"source": source, "req_code": code, "status": status, "testability": score},
        )
    except Exception:  # noqa: BLE001
        pass  # 索引失败不阻塞录入
    return {"id": rid, "code": code, "status": status, "testability": score, "report": json.loads(req.parse_report)}


@app.post("/api/requirements/ingest")
async def ingest_requirement(data: RequirementIngestIn):
    body = data.model_dump()
    title = (body.get("title") or "").strip()
    text = (body.get("body") or body.get("text") or "").strip()
    if not title or not text:
        raise ApiError(1001, "title 与 body 必填")
    repo_id = int(body.get("repo_id") or 0)
    source = body.get("source") or "paste"
    return ok(ingest_one(title, text, repo_id, source))


@app.get("/api/requirements")
def list_requirements(db: Session = Depends(get_db)):
    rows = db.query(Requirements).order_by(Requirements.id.desc()).limit(200).all()
    by_status = _count_by(db, Requirements.status)
    return ok(
        {
            "total": len(rows),
            "by_status": by_status,
            "items": [
                {
                    "id": r.id,
                    "code": r.code,
                    "title": r.title,
                    "status": r.status,
                    "testability": r.testability_score,
                    "repo_id": r.repo_id,
                    "trace_id": r.trace_id,
                    "report": json.loads(r.parse_report) if r.parse_report else {},
                    "quality_profile": json.loads(r.quality_profile) if r.quality_profile else {},
                    "created_at": r.created_at.isoformat(),
                }
                for r in rows
            ],
        }
    )


@app.post("/api/requirements/{req_id}/confirm")
async def confirm_requirement(req_id: int, data: RequirementConfirmIn):
    """冲突确认 / 人审通过 → 已生效 → 自动编排生成（P1 主流程）。"""
    body = data.model_dump()
    action = body.get("action", "approve")  # approve | reject
    with get_session() as sess:
        req = sess.get(Requirements, req_id)
        if req is None:
            raise ApiError(404, "需求不存在", 404)
        code = req.code
        repo_id = req.repo_id or 0
        report = json.loads(req.parse_report) if req.parse_report else {}
        profile = json.loads(req.quality_profile) if req.quality_profile else {}

        if action == "reject":
            req.status = "已打回"
            req.manual_interventions += 1
            profile["G0"] = {"ok": False, "note": f"人工打回: {body.get('note', '')}", "ts": datetime.now().isoformat()}
            req.quality_profile = json.dumps(profile, ensure_ascii=False)
            sess.commit()
            emit("需求", "qa-reviewer", f"需求 {code} 人工打回", req_code=code)
            return ok({"code": code, "status": "已打回"})

        # 冲突确认：先更新 Wiki 规则页（业务流/模块页追加新规则）
        new_rules = report.get("new_rules") or []
        if new_rules:
            page = sess.query(WikiPages).filter(WikiPages.repo_id == repo_id, WikiPages.level == "module").order_by(WikiPages.id).first()
            if page is not None:
                page.content_md += "\n## 需求确认新增规则\n\n" + "\n".join(f"- {r}" for r in new_rules) + "\n"
                page.rev += 1
                page.stale = False
        req.status = "已生效"
        req.manual_interventions += 1
        profile.setdefault("确认记录", []).append({"action": "confirm", "ts": datetime.now().isoformat(), "conflict": bool(report.get("conflict"))})
        req.quality_profile = json.dumps(profile, ensure_ascii=False)
        sess.commit()

    # 自动编排：需求驱动生成（后台）
    target = _derive_target(report, repo_id)
    gen = new_generation_threaded({"function": target, "repo_id": repo_id, "layer": "ut", "source_req": code})
    emit("需求", "req-svc", f"需求 {code} 已生效，自动编排生成 {gen['generation_id']}（目标 {target}）", req_code=code)
    return ok({"code": code, "status": "已生效", "generation_id": gen["generation_id"], "target": target})


def _derive_target(report: dict, repo_id: int = 0) -> str:
    """从需求原文/抽取规则的关键词路由生成目标函数。

    只对需求文本与规则匹配关键词（不看解析报告的 pipeline 字段，避免"规则抽取"等
    流程字样劫持路由）；路由目标必须在该仓库已索引，否则回退仓库内调用边最多的函数
    （需求绑定的仓库里总得有个可测目标，NOT_FOUND 不算编排完成）。
    """
    story = json.dumps(report.get("story") or "", ensure_ascii=False) + json.dumps(
        report.get("rules") or [], ensure_ascii=False
    )
    candidates: list[str] = []
    if any(kw in story for kw in ("脱敏", "掩码", "sanitize", "泄露", "凭证", "令牌", "token")):
        candidates.append("sanitize_text")
    if any(kw in story for kw in ("规则抽取", "抽取规则", "结构化规则", "extract_rules")):
        candidates.append("extract_rules")
    candidates.append("create_order")

    from sqlalchemy import func

    from app.models import CallEdges, Functions

    with get_session() as sess:
        if not repo_id:
            return candidates[0]
        for t in candidates:
            if sess.query(Functions).filter(Functions.repo_id == repo_id, Functions.name == t).first() is not None:
                return t
        row = (
            sess.query(Functions.id, Functions.name, func.count(CallEdges.id))
            .outerjoin(CallEdges, CallEdges.caller_id == Functions.id)
            .filter(Functions.repo_id == repo_id)
            .group_by(Functions.id, Functions.name)
            .order_by(func.count(CallEdges.id).desc())
            .first()
        )
        if row is not None:
            return row[1]
    return candidates[0]


def new_generation_threaded(payload: dict) -> dict:
    from app.api.generations import new_generation

    return new_generation(payload)


@app.get("/api/requirements/{req_code}/similar")
def similar_for_requirement(req_code: str, limit: int = 5, db: Session = Depends(get_db)):
    """相似用例 RAG 检索（需求文本 → 已入库用例 top-k）。"""
    from app.services.knowledge.rag import similar_cases

    req = db.query(Requirements).filter(Requirements.code == req_code).first()
    if req is None:
        raise ApiError(404, "需求不存在", 404)
    text = f"{req.title} {req.body[:400]}"
    return ok(similar_cases(text, limit=limit))


@app.get("/api/quality/requirements")
def quality_pipeline(db: Session = Depends(get_db)):
    """需求质量流水线：每个需求 G0~G5 六关卡状态 + 质量分 + 人工介入次数。"""
    reqs = db.query(Requirements).order_by(Requirements.id.desc()).limit(100).all()
    out = []
    for r in reqs:
        profile = json.loads(r.quality_profile) if r.quality_profile else {}
        cases = db.query(Cases).filter(Cases.source_req == r.code).all()
        runs = db.query(Runs).filter(Runs.req_code == r.code).all()
        defects_open = db.query(Defects).filter(Defects.req_code == r.code, Defects.status != "已关闭").count()
        gates: dict[str, dict[str, object]] = {
            "G0 可测性": {"ok": r.testability_score >= 80 and r.status != "已打回", "detail": f"评分 {r.testability_score:.0f}"},
            "G1 知识就绪": {"ok": bool(db.query(WikiPages).filter(WikiPages.repo_id == r.repo_id).count()) if r.repo_id else False, "detail": "wiki 编译完成" if r.repo_id else "未绑定仓库"},
            "G2 用例覆盖": {"ok": len(cases) > 0, "detail": f"{len(cases)} 用例"},
            "G3 执行验证": {"ok": bool(runs) and all(x.status == "success" for x in runs), "detail": f"{sum(1 for x in runs if x.status == 'success')}/{len(runs)} run 通过"},
            "G4 缺陷清零": {"ok": defects_open == 0, "detail": "无未关闭缺陷" if defects_open == 0 else f"{defects_open} 个未关闭缺陷"},
            "G5 准出报告": {"ok": r.status == "已生效" and bool(runs) and all(x.status == "success" for x in runs), "detail": "生成验证完成后自动判定"},
        }
        passed = sum(1 for g in gates.values() if g["ok"])
        weight = {"G0 可测性": 0.2, "G1 知识就绪": 0.1, "G2 用例覆盖": 0.25, "G3 执行验证": 0.25, "G4 缺陷清零": 0.1, "G5 准出报告": 0.1}
        quality_score = round(sum(weight[k] for k, g in gates.items() if g["ok"]) * 100, 1)
        out.append(
            {
                "code": r.code,
                "title": r.title,
                "status": r.status,
                "gates": gates,
                "gates_passed": passed,
                "quality_score": quality_score,
                "manual_interventions": r.manual_interventions,
                "cases": len(cases),
                "runs": len(runs),
                "profile": profile,
            }
        )
    return ok(out)
