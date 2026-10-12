"""trace-svc：缺陷闭环 —— 自动建缺陷（CreateFromRun）+ 定向回归（TriggerRegression）+ 通用流转（UpdateDefect）。

流转引擎参照 Bugzilla update 流水线 + Jira workflow 三件套落地：
- 转换图存 defect_workflow 表（数据驱动，管理员改表即可调流程）
- validator：require_comment 边必须带评论；转入"已关闭"必填合法结案原因
- post-function：重开自动清空结案原因/重复指向；标记重复强制 resolution=重复
- 每次变更落 defect_activity 字段级 diff（who/field/旧值→新值），动作以带类型评论入流
- updated_at 乐观锁：expected_updated_at 不匹配拒绝写入（防并发互覆）
"""

import json
import logging
import uuid

from sqlalchemy.orm import Session

from app.core.trace import emit
from app.db.session import get_session
from app.models import Cases, Defects, DefectActivity, DefectComments, DefectWorkflow, Runs

log = logging.getLogger("trace-svc.defect")

# 平台没有真实人员目录，不编造指派对象——缺陷默认未指派，由 QA 在缺陷页人工流转
DEFAULT_ASSIGNEE = "未指派"

# 合法结案原因 / 优先级（与前端选项保持一致）
RESOLUTIONS = ("已修复", "非缺陷", "重复", "无法复现", "暂缓")
PRIORITIES = ("P0", "P1", "P2", "P3")


class DefectConflict(Exception):
    """乐观锁冲突：expected_updated_at 与库内 updated_at 不一致。"""


class DefectTransitionError(Exception):
    """流转校验不过（非法转换/缺结案原因/缺评论/重复指向无效）。"""


# ---------------- 活动日志 / 评论 ----------------


def log_activity(defect_id: int, actor: str, field: str, old_value: str, new_value: str) -> None:
    """公开入口：独立会话写一行字段级变更（供 API 层绕过流转引擎的系统动作使用）。"""
    with get_session() as sess:
        sess.add(DefectActivity(defect_id=defect_id, actor=actor, field=field, old_value=old_value, new_value=new_value))
        sess.commit()


def _log_activity(sess: Session, defect_id: int, actor: str, field: str, old_value: str, new_value: str) -> None:
    sess.add(DefectActivity(defect_id=defect_id, actor=actor, field=field, old_value=old_value, new_value=new_value))


def _add_comment(sess: Session, defect_id: int, who: str, body: str, ctype: str = "normal") -> None:
    sess.add(DefectComments(defect_id=defect_id, who=who, body=body, type=ctype))


# ---------------- 自动建单 ----------------


def _assignee(target_label: str) -> str:
    """平台没有真实人员目录，不编造指派对象——默认未指派，由 QA 在缺陷页人工流转。"""
    return DEFAULT_ASSIGNEE


def create_from_run(run_id: str, case_codes: list[str], req_code: str, trace_id: str, reason: str, title: str = "") -> dict:
    """失败执行 → 自动建缺陷（四向关联 run/用例/需求/traceID + 自动指派）。

    内容可读化：标题带失败目标（模块/函数），detail 结构化（原因+失败用例清单），
    建单系统评论直接列失败用例——人打开缺陷第一眼就知道哪里坏了。
    """
    similar = ""
    with get_session() as sess:
        run = sess.query(Runs).filter(Runs.code == run_id).first()
        case_rows = sess.query(Cases).filter(Cases.code.in_(case_codes)).all() if case_codes else []
        # 失败目标定位：run.target（生成流=目标函数全名）→ 用例 module → 用例 target_function
        target_label = (run.target or "") if run is not None else ""
        if not target_label and case_rows:
            target_label = case_rows[0].module or case_rows[0].target_function or ""
        severity = "致命" if len(case_codes) > 5 else "严重" if case_codes else "一般"
        base_title = (title or "").strip() or (reason or "").strip() or f"run {run_id} 执行失败"
        defect = Defects(
            code=f"BUG-{uuid.uuid4().hex[:6].upper()}",
            title=(f"{target_label}：{base_title}" if target_label else base_title)[:500],
            origin_run=run_id,
            case_codes=json.dumps(case_codes, ensure_ascii=False),
            req_code=req_code,
            severity=severity,
            status="新建",
            assignee=_assignee(target_label),
            trace_id=trace_id,
            detail=json.dumps(
                {
                    "reason": reason,
                    "module": target_label,
                    "failed": len(case_codes),
                    "run": run_id,
                    "cases": [{"code": c.code, "title": c.title} for c in case_rows[:20]],
                },
                ensure_ascii=False,
            ),
        )
        sess.add(defect)
        sess.flush()  # 取 id，建单动作本身也进活动/评论流
        _log_activity(sess, defect.id, "runner-svc", "状态", "", "新建")
        case_lines = "\n".join(f"- {c.code} {c.title}" for c in case_rows[:10])
        more = f"\n…等共 {len(case_codes)} 条" if len(case_codes) > 10 else ""
        _add_comment(
            sess,
            defect.id,
            "runner-svc",
            (
                f"失败执行自动建单：{reason or '未说明原因'}\n失败用例（{len(case_codes)}）：\n{case_lines}{more}"
                if case_rows
                else f"失败执行自动建单：{reason or '未说明原因'}（无关联用例记录）"
            ),
            "system",
        )
        # 同源复现检测（Jira duplicates 前置思路）：与仍开启的缺陷共享失败用例 → 在旧缺陷上留痕，人工决定是否合并
        if case_codes:
            open_rows = sess.query(Defects).filter(Defects.status != "已关闭", Defects.id != defect.id).all()
            case_set = set(case_codes)
            for od in open_rows:
                try:
                    overlap = case_set & set(json.loads(od.case_codes or "[]"))
                except json.JSONDecodeError:
                    continue
                if overlap:
                    similar = od.code
                    _add_comment(
                        sess,
                        od.id,
                        "runner-svc",
                        f"疑似同源复现：新缺陷 {defect.code}（run {run_id}）再次失败，共用用例 {', '.join(sorted(overlap))}。",
                        "system",
                    )
                    break
        sess.commit()
        emit(
            "缺陷",
            "runner-svc",
            f"失败自动建缺陷 {defect.code}（{severity}，指派 {defect.assignee}）关联 run={run_id} 用例={len(case_codes)}"
            + (f"，疑似同源 {similar}" if similar else ""),
            req_code=req_code,
            trace_id=trace_id,
        )
        # 缺陷入检索库（kind='defect'）：生成上下文 bugs 检索路与全局搜索的语料源
        try:
            from app.services.knowledge.rag import ensure_rag_documents_table, index_document

            ensure_rag_documents_table()
            index_document(
                f"defect:{defect.code}",
                "defect",
                f"缺陷 {defect.code} {defect.title}",
                f"缺陷 {defect.code}（{severity}，状态：新建）\n关联 run：{run_id}\n关联用例：{', '.join(case_codes) or '-'}\n关联需求：{req_code or '-'}\n失败原因：{reason}\n详情：{defect.detail[:1200]}",
                repo_id=_infer_repo_id(req_code),
                meta={"source": "defect-created", "defect_code": defect.code, "status": defect.status, "severity": severity},
            )
        except Exception:  # noqa: BLE001
            log.warning("缺陷 %s 检索索引失败（不阻塞建单）", defect.code, exc_info=True)
        return {
            "id": defect.id,
            "code": defect.code,
            "title": defect.title,
            "severity": severity,
            "assignee": defect.assignee,
            "status": defect.status,
            "similar_code": similar,
        }


def _infer_repo_id(req_code: str) -> int:
    """缺陷没有直接 repo 关联：经需求反查；单仓库部署兜底取唯一仓库（与 lesson 沉淀同规则）。"""
    from app.models import Repos, Requirements

    with get_session() as sess:
        if req_code:
            row = sess.query(Requirements.repo_id).filter(Requirements.code == req_code).first()
            if row and row[0]:
                return int(row[0])
        ids = [r[0] for r in sess.query(Repos.id).all()]
        return int(ids[0]) if len(ids) == 1 else 0


# ---------------- 通用流转更新（流转引擎） ----------------


def _resolve_dupe_target(sess: Session, self_id: int, target_code: str) -> str:
    """沿 dupe 链找最终目标（Bugzilla _resolve_ultimate_dup_id 防环思路）。"""
    seen = {self_id}
    code = target_code.strip()
    for _ in range(8):  # 链深上限，防御异常数据
        row = sess.query(Defects).filter(Defects.code == code).first()
        if row is None:
            raise DefectTransitionError(f"重复指向的目标缺陷不存在：{code}")
        if row.id in seen:
            raise DefectTransitionError(f"重复指向成环：{code}")
        seen.add(row.id)
        if not row.dupe_of:
            return row.code
        code = row.dupe_of
    raise DefectTransitionError(f"重复链过深，拒绝指向：{target_code}")


def update_defect(defect_id: int, payload: dict) -> dict:
    """通用流转更新：转换校验 → diff → 活动+评论 → 事务提交 → 沉淀/索引。

    返回 {code, status, resolution, dupe_of, changes:{字段:[旧,新]}, lesson}。
    校验失败抛 DefectTransitionError，并发冲突抛 DefectConflict，由 API 层转业务错误。
    """
    actor = (payload.get("actor") or "qa").strip() or "qa"
    new_status = (payload.get("status") or "").strip()
    comment = (payload.get("comment") or "").strip()
    expected = (payload.get("expected_updated_at") or "").strip()

    with get_session() as sess:
        d = sess.get(Defects, defect_id)
        if d is None:
            raise KeyError(f"defect {defect_id} not found")

        # 乐观锁（midair collision 防护）
        if expected and (d.updated_at is None or d.updated_at.isoformat() != expected):
            raise DefectConflict(f"缺陷已被他人更新（当前状态：{d.status}），请刷新后重试")

        old = {
            "status": d.status,
            "resolution": d.resolution or "",
            "priority": d.priority or "",
            "assignee": d.assignee or "",
            "fix_version": d.fix_version or "",
            "dupe_of": d.dupe_of or "",
        }

        # 1) 流转校验：转换边必须存在于 defect_workflow
        if new_status and new_status != d.status:
            edge = (
                sess.query(DefectWorkflow)
                .filter(DefectWorkflow.from_status == d.status, DefectWorkflow.to_status == new_status)
                .first()
            )
            if edge is None:
                raise DefectTransitionError(f"非法流转：{d.status} → {new_status}（转换图无此边）")
            if edge.require_comment and not comment:
                raise DefectTransitionError(f"流转 {d.status} → {new_status} 必须附带评论")

        # 2) 结案校验（validator）：转入已关闭必填合法结案原因
        new_resolution = (payload.get("resolution") or "").strip()
        target_status = new_status or d.status
        if target_status == "已关闭":
            if not new_resolution:
                raise DefectTransitionError("关闭缺陷必须选择结案原因（已修复/非缺陷/重复/无法复现/暂缓）")
            if new_resolution not in RESOLUTIONS:
                raise DefectTransitionError(f"非法结案原因：{new_resolution}")
        elif new_resolution and new_resolution not in RESOLUTIONS:
            raise DefectTransitionError(f"非法结案原因：{new_resolution}")

        # 3) 重复指向（post-function）：扁平化到链尾 + 强制 resolution=重复
        new_dupe = (payload.get("dupe_of") or "").strip()
        if new_dupe and new_dupe != old["dupe_of"]:
            if target_status != "已关闭":
                raise DefectTransitionError("标记重复请同时将状态流转到已关闭（结案原因=重复）")
            new_dupe = _resolve_dupe_target(sess, defect_id, new_dupe)
            new_resolution = "重复"

        # 4) 重开（post-function）：清空结案原因与重复指向
        reopen = bool(old["status"] == "已关闭" and new_status and new_status != "已关闭")
        if reopen:
            new_resolution = ""
            new_dupe = ""

        # 5) 组装 diff：空字符串字段视为"不修改"（除重开清空外）
        candidate = {
            "status": new_status or d.status,
            "resolution": new_resolution or ("" if reopen else old["resolution"]),
            "priority": (payload.get("priority") or "").strip() or old["priority"],
            "assignee": (payload.get("assignee") or "").strip() or old["assignee"],
            "fix_version": (payload.get("fix_version") or "").strip() or old["fix_version"],
            "dupe_of": new_dupe or ("" if reopen else old["dupe_of"]),
        }
        changes = {f: [old[f], candidate[f]] for f in candidate if candidate[f] != old[f]}

        if not changes and not comment:
            return {"code": d.code, "status": d.status, "resolution": old["resolution"], "dupe_of": old["dupe_of"], "changes": {}, "lesson": ""}

        # 6) 落库：字段值 + 字段级活动 + 伴随评论 + 重复联动评论
        for f, [_o, n] in changes.items():
            setattr(d, f, n)
        for f, [_o, n] in changes.items():
            _log_activity(sess, d.id, actor, f, _o, n)
        if comment:
            _add_comment(sess, d.id, actor, comment, "normal")
        if "dupe_of" in changes:
            _add_comment(sess, d.id, actor, f"已标记为 {candidate['dupe_of']} 的重复（结案原因：重复）。", "dupe")
            target = sess.query(Defects).filter(Defects.code == candidate["dupe_of"]).first()
            if target is not None:
                _add_comment(sess, target.id, actor, f"缺陷 {d.code} 被标记为其重复（报告人请关注目标缺陷进展）。", "dupe")
        code, req_code = d.code, d.req_code
        sess.commit()

    # 7) 事务后副作用：trace 流 + 关闭沉淀教训 + 检索索引刷新
    if changes:
        emit("缺陷", actor, f"缺陷 {code} 变更：" + "，".join(f"{f} {v[0] or '∅'}→{v[1]}" for f, v in changes.items()), req_code=req_code)
    if comment:
        emit("缺陷", actor, f"缺陷 {code} 新增评论（{len(comment)} 字）", req_code=req_code)
    lesson = ""
    if changes:
        if "status" in changes and candidate["status"] == "已关闭":
            lesson = sediment_lesson(code)
        _refresh_index(code)
    return {
        "code": code,
        "status": candidate["status"],
        "resolution": candidate["resolution"],
        "dupe_of": candidate["dupe_of"],
        "changes": changes,
        "lesson": lesson,
    }


# ---------------- 关闭沉淀 / 索引刷新 ----------------


def sediment_lesson(code: str) -> str:
    """关闭沉淀（LightRAG 增量学习思路）：失败详情 + 已验证修复建议入 kind='lesson'，生成同函数用例时自动召回。"""
    try:
        from app.models import Requirements, Repos
        from app.services.knowledge.rag import ensure_rag_documents_table, index_document

        with get_session() as sess:
            d = sess.query(Defects).filter(Defects.code == code).first()
            if d is None:
                return ""
            req, title = d.req_code, d.title
            detail, suggestion = d.detail, d.suggestion
            case_codes, severity = d.case_codes, d.severity
            resolution = d.resolution
        repo_id = 0
        with get_session() as sess:
            if req:
                row = sess.query(Requirements.repo_id).filter(Requirements.code == req).first()
                repo_id = int(row[0] or 0) if row and row[0] else 0
            if not repo_id:
                # 历史需求/缺陷普遍缺 repo 关联：单仓库部署直接归属唯一仓库，
                # 多仓库且无法判定时才放弃沉淀（lesson 检索按 repo 精确过滤）
                repo_ids = [r[0] for r in sess.query(Repos.id).all()]
                if len(repo_ids) == 1:
                    repo_id = int(repo_ids[0])
        if not repo_id:
            return ""
        content = (
            f"# 缺陷 {code}：{title}\n\n严重度：{severity}，结案原因：{resolution or '-'}\n关联需求：{req or '-'}\n关联用例：{case_codes or '-'}\n\n"
            f"## 失败详情\n{detail[:1500]}\n\n## 修复建议（回归通过后关闭，已验证）\n{suggestion[:1500]}\n\n"
            f"生成该模块测试用例时应覆盖本缺陷对应的异常分支，避免回归。"
        )
        ensure_rag_documents_table()
        index_document(
            f"lesson:defect:{code}",
            "lesson",
            f"缺陷教训 {code} {title}",
            content,
            repo_id=repo_id,
            meta={"source": "defect-closed", "defect_code": code, "req_code": req},
        )
        emit("知识", "lesson", f"缺陷 {code} 关闭 → 教训入检索库", req_code=req)
        return f"教训已沉淀（repo {repo_id}，生成时自动召回）"
    except Exception as exc:  # noqa: BLE001
        return f"教训沉淀失败: {exc}"


def _refresh_index(code: str) -> None:
    """缺陷本体索引同步刷新（状态/结案/修复建议进语料）。"""
    try:
        from app.services.knowledge.rag import ensure_rag_documents_table, index_document

        with get_session() as sess:
            d = sess.query(Defects).filter(Defects.code == code).first()
            if d is None:
                return
            ensure_rag_documents_table()
            index_document(
                f"defect:{d.code}",
                "defect",
                f"缺陷 {d.code} {d.title}",
                f"缺陷 {d.code}（{d.severity}，状态：{d.status}，结案：{d.resolution or '-'}）\n关联 run：{d.origin_run}\n"
                f"关联用例：{d.case_codes}\n关联需求：{d.req_code or '-'}\n失败详情：{d.detail[:1000]}\n修复建议：{d.suggestion[:1200]}",
                repo_id=_infer_repo_id(d.req_code),
                meta={"source": "defect-status", "defect_code": d.code, "status": d.status, "severity": d.severity},
            )
    except Exception:  # noqa: BLE001
        log.warning("缺陷 %s 检索索引刷新失败", code, exc_info=True)


# ---------------- 定向回归 ----------------


def trigger_regression(defect_id: int, trace_id: str) -> dict:
    """自动回归：只重跑缺陷关联用例 → 通过自动关闭 / 失败重开。系统动作同样落活动+评论。"""
    with get_session() as sess:
        d = sess.get(Defects, defect_id)
        if d is None:
            raise KeyError(f"defect {defect_id} not found")
        case_codes = json.loads(d.case_codes or "[]")
        case_rows = sess.query(Cases).filter(Cases.code.in_(case_codes)).all() if case_codes else []
        code_file = ""
        for c in case_rows:
            try:
                data = json.loads(c.schema_json or "{}")
            except json.JSONDecodeError:
                continue
            # 入库 schema 为 suite_cases 元素：code_file 在顶层或嵌套 schema_json 字段
            cf = data.get("code_file")
            if not cf and isinstance(data.get("schema_json"), str):
                try:
                    cf = json.loads(data["schema_json"]).get("code_file")
                except json.JSONDecodeError:
                    cf = None
            if cf:
                code_file = cf
                break
        if not case_rows or not code_file:
            old_status = d.status
            d.status = "已关闭"
            d.trace_id = d.trace_id or trace_id
            _log_activity(sess, d.id, "plan-svc", "状态", old_status, "已关闭")
            _add_comment(sess, d.id, "plan-svc", "无可回归用例，按遗留结论关闭（系统动作，未设置结案原因）。", "system")
            sess.commit()
            emit("缺陷", "plan-svc", f"缺陷 {d.code} 无可回归用例，按遗留结论关闭", req_code=d.req_code, trace_id=trace_id)
            return {"run_code": "-", "pass_count": 0, "pass_total": 0, "status": "已关闭", "defect_code": d.code}

        suite_cases = [
            {
                "code": c.code,
                "title": c.title,
                "layer": c.layer,
                "module": c.module,
                "category": c.category,
                "schema_json": c.schema_json,
                "source_req": c.source_req,
            }
            for c in case_rows
        ]
        run_code = f"RUN-{uuid.uuid4().hex[:8].upper()}"

    from app.services.runner.api import execute as runner_execute

    report = runner_execute(run_code, suite_cases, repo_id=0, trace_id=trace_id)
    passed = int(report.get("pass_count") or 0)
    total = int(report.get("pass_total") or 0)
    with get_session() as sess:
        d = sess.get(Defects, defect_id)
        old_status = d.status
        d.status = "已关闭" if passed == total and total > 0 else "待回归"
        _log_activity(sess, d.id, "plan-svc", "状态", old_status, d.status)
        if d.status == "已关闭":
            # post-function：回归通过自动关闭 → 结案原因=已修复
            old_res = d.resolution or ""
            d.resolution = "已修复"
            _log_activity(sess, d.id, "plan-svc", "结案原因", old_res, "已修复")
            _add_comment(sess, d.id, "plan-svc", f"回归通过：重跑关联用例 {passed}/{total}，自动关闭（结案原因：已修复）。", "system")
        else:
            _add_comment(sess, d.id, "plan-svc", f"回归未通过：重跑关联用例 {passed}/{total}，重开待回归。", "system")
        sess.commit()
    emit(
        "缺陷",
        "plan-svc",
        f"缺陷回归 {d.code}: 重跑关联用例 {passed}/{total} → {d.status}",
        req_code=d.req_code,
        trace_id=trace_id,
    )
    if d.status == "已关闭":
        sediment_lesson(d.code)
        _refresh_index(d.code)
    return {"run_code": report.get("run_id", run_code), "pass_count": passed, "pass_total": total, "status": d.status, "defect_code": d.code}
