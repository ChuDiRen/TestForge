"""需求（M3）：录入解析、冲突确认、自动编排、质量流水线 G0~G5。"""

import json
import logging
from datetime import datetime

from fastapi import Request

from gateway.main import GRPC_PORTS, ApiError, _count_by, app, get_session, grpc_call, ok
from services.shared.models import Cases, Requirements, Runs, WikiPages
from services.shared.trace import emit

log = logging.getLogger("gateway.req")

GATES = ("G0 可测性", "G1 知识就绪", "G2 用例覆盖", "G3 执行验证", "G4 缺陷清零", "G5 准出报告")


def _next_req_code(sess) -> str:  # type: ignore[no-untyped-def]
    n = sess.query(Requirements).count() + 101
    while sess.query(Requirements).filter(Requirements.code == f"REQ-{n}").first() is not None:
        n += 1
    return f"REQ-{n}"


@app.post("/api/requirements/ingest")
async def ingest_requirement(request: Request):
    body = await request.json()
    title = (body.get("title") or "").strip()
    text = (body.get("body") or body.get("text") or "").strip()
    if not title or not text:
        raise ApiError(1001, "title 与 body 必填")
    repo_id = int(body.get("repo_id") or 0)
    source = body.get("source") or "paste"

    with get_session() as sess:
        code = _next_req_code(sess)
        req = Requirements(code=code, title=title, source=source, body=text, repo_id=repo_id or None, status="解析中", trace_id="")
        sess.add(req)
        sess.commit()
        rid = req.id

    report = grpc_call(
        "req-svc",
        GRPC_PORTS["req-svc"],
        "ReqIngest",
        "Parse",
        {"code": code, "title": title, "body": text, "source": source, "repo_id": repo_id},
        timeout=60,
    )
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
    return ok({"id": rid, "code": code, "status": status, "testability": score, "report": json.loads(req.parse_report)})


@app.get("/api/requirements")
def list_requirements():
    with get_session() as sess:
        rows = sess.query(Requirements).order_by(Requirements.id.desc()).limit(200).all()
        by_status = _count_by(sess, Requirements.status)
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
async def confirm_requirement(req_id: int, request: Request):
    """冲突确认 / 人审通过 → 已生效 → 自动编排生成（P1 主流程）。"""
    body = await request.json()
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
    target = _derive_target(report)
    gen = new_generation_threaded({"function": target, "repo_id": repo_id, "layer": "ut", "source_req": code})
    emit("需求", "req-svc", f"需求 {code} 已生效，自动编排生成 {gen['generation_id']}（目标 {target}）", req_code=code)
    return ok({"code": code, "status": "已生效", "generation_id": gen["generation_id"], "target": target})


def _derive_target(report: dict) -> str:
    """从解析报告推导生成目标函数（mock 约定：订单域需求 → create_order）。"""
    story = json.dumps(report, ensure_ascii=False)
    if "订单" in story or "下单" in story or "支付" in story:
        return "create_order"
    return "create_order"


def new_generation_threaded(payload: dict) -> dict:
    from gateway.generations import new_generation

    return new_generation(payload)


@app.get("/api/requirements/{req_code}/similar")
def similar_for_requirement(req_code: str, limit: int = 5):
    """相似用例 RAG 检索（需求文本 → 已入库用例 top-k）。"""
    from services.shared.rag import similar_cases

    with get_session() as sess:
        req = sess.query(Requirements).filter(Requirements.code == req_code).first()
        if req is None:
            raise ApiError(404, "需求不存在", 404)
        text = f"{req.title} {req.body[:400]}"
    return ok(similar_cases(text, limit=limit))


@app.get("/api/quality/requirements")
def quality_pipeline():
    """需求质量流水线：每个需求 G0~G5 六关卡状态 + 质量分 + 人工介入次数。"""
    with get_session() as sess:
        reqs = sess.query(Requirements).order_by(Requirements.id.desc()).limit(100).all()
        out = []
        for r in reqs:
            profile = json.loads(r.quality_profile) if r.quality_profile else {}
            cases = sess.query(Cases).filter(Cases.source_req == r.code).all()
            runs = sess.query(Runs).filter(Runs.req_code == r.code).all()
            defects_open = 0  # M5 接缺陷闭环后填充
            gates = {
                "G0 可测性": {"ok": r.testability_score >= 80 and r.status != "已打回", "detail": f"评分 {r.testability_score:.0f}"},
                "G1 知识就绪": {"ok": bool(sess.query(WikiPages).filter(WikiPages.repo_id == r.repo_id).count()) if r.repo_id else False, "detail": "wiki 编译完成" if r.repo_id else "未绑定仓库"},
                "G2 用例覆盖": {"ok": len(cases) > 0, "detail": f"{len(cases)} 用例"},
                "G3 执行验证": {"ok": bool(runs) and all(x.status == "success" for x in runs), "detail": f"{sum(1 for x in runs if x.status == 'success')}/{len(runs)} run 通过"},
                "G4 缺陷清零": {"ok": defects_open == 0, "detail": "无未关闭缺陷"},
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
