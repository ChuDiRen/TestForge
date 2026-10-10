"""任务队列：jobs 表持久化 + 应用进程内 worker 池。

生成/回归全部以任务形式入队执行：可观测（/api/jobs）、可恢复（崩溃时 running 重排队）、
可限流（job_workers 并发上限，避免 DeepSeek 并发打爆）。任务本身幂等——重跑等价。
"""

from __future__ import annotations

import json
import logging
import threading

from app.core.config import get_settings
from app.db.session import get_session
from app.models import Jobs

log = logging.getLogger("app.api.jobs")

_wake = threading.Event()


def enqueue(kind: str, payload: dict, gen_code: str = "") -> dict:
    """入队一个任务并唤醒 worker。返回 {job_code, status}。"""
    import uuid

    code = f"JOB-{uuid.uuid4().hex[:8].upper()}"
    with get_session() as sess:
        sess.add(
            Jobs(code=code, kind=kind, status="queued", payload_json=json.dumps(payload, ensure_ascii=False), gen_code=gen_code)
        )
        sess.commit()
    _wake.set()
    log.info("job queued %s kind=%s", code, kind)
    return {"job_code": code, "status": "queued"}


def recover_and_start() -> int:
    """启动恢复：把上次进程中断遗留的 running 任务重排为 queued，再拉起 worker 线程池。"""
    with get_session() as sess:
        n = sess.query(Jobs).filter(Jobs.status == "running").update({"status": "queued"}, synchronize_session=False)
        sess.commit()
    if n:
        log.warning("requeued %d interrupted job(s)", n)
    workers = max(1, get_settings().job_workers)
    for i in range(workers):
        threading.Thread(target=_worker_loop, args=(i,), daemon=True, name=f"job-worker-{i}").start()
    return workers


def _claim_one() -> dict | None:
    """抢占一条 queued 任务（FOR UPDATE SKIP LOCKED，多 worker 不重复消费）。"""
    with get_session() as sess:
        row = (
            sess.query(Jobs)
            .filter(Jobs.status == "queued")
            .order_by(Jobs.id.asc())
            .with_for_update(skip_locked=True)
            .first()
        )
        if row is None:
            return None
        row.status = "running"
        sess.commit()
        return {"code": row.code, "kind": row.kind, "payload": json.loads(row.payload_json or "{}"), "gen_code": row.gen_code}


def _worker_loop(idx: int) -> None:
    while True:
        job = None
        try:
            job = _claim_one()
        except Exception as exc:  # noqa: BLE001
            log.warning("worker %d claim failed: %s", idx, exc)
        if job is None:
            _wake.wait(timeout=1.0)
            _wake.clear()
            continue
        try:
            _run(job)
            with get_session() as sess:
                row = sess.query(Jobs).filter(Jobs.code == job["code"]).first()
                if row is not None:
                    row.status = "done"
                    sess.commit()
        except Exception as exc:  # noqa: BLE001
            log.exception("job %s failed", job["code"])
            with get_session() as sess:
                row = sess.query(Jobs).filter(Jobs.code == job["code"]).first()
                if row is not None:
                    row.status = "failed"
                    row.error = str(exc)[:2000]
                    sess.commit()


def _run(job: dict) -> None:
    payload = job["payload"]
    if job["kind"] == "generate":
        from app.api.generations import run_pipeline

        run_pipeline(
            job["gen_code"],
            int(payload.get("repo_id") or 0),
            str(payload.get("function") or ""),
            str(payload.get("layer") or "ut"),
            str(payload.get("source_req") or ""),
            str(payload.get("trace_id") or ""),
        )
    elif job["kind"] == "regression":
        from app.api.regression import run_regression

        run_regression(int(payload.get("repo_id") or 0), list(payload.get("case_codes") or []), str(payload.get("trace_id") or ""))
    else:
        raise ValueError(f"未知任务类型: {job['kind']}")
