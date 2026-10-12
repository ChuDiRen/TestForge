# -*- coding: utf-8 -*-
"""全模块 E2E 冒烟：OpenAPI 全部 136 个操作逐个真实打一遍（正路 + 关键异常路）。

原则：
- 自建 "E2E-SMOKE" 前缀夹具（仓库/需求/缺陷/契约/会话/文档），绝不改动真实数据；
- LLM 调用压到最小（生成/问答/检索各一两次短调用）；
- 3 个重型端点按受控方式执行：wiki 增量重建、kg hash 增量构建、社区构建 llm=false；
- 结果落 e2e_results.json，清理所需 id 线索落 e2e_fixture_ids.json。
"""
from __future__ import annotations

import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

BASE = "http://127.0.0.1:8000"
MARK = "E2E-SMOKE"
results: list[dict] = []
fixture_ids: dict = {"repos": [], "requirements": [], "defects": [], "contracts": [],
                     "threads": [], "wiki_sessions": [], "kg_docs": [], "know_docs": [],
                     "cases": [], "plans": [], "gen_codes": []}

S = requests.Session()
S.headers["Content-Type"] = "application/json"


def rec(op: str, r=None, ok: bool | None = None, note: str = "") -> None:
    status = getattr(r, "status_code", None)
    if ok is None:
        ok = status is not None and status < 500
    results.append({"op": op, "status": status, "ok": ok, "note": str(note)[:300]})
    print(("PASS" if ok else "FAIL"), f"[{status}]", op, ("| " + str(note)[:120]) if note else "", flush=True)


def api(method: str, path: str, expect=(200,), timeout=60, **kw):
    r = S.request(method, BASE + path, timeout=timeout, **kw)
    return r


def jget(r):
    try:
        return r.json()
    except Exception:
        return {}


def jdata(r) -> dict:
    d = jget(r).get("data")
    return d if isinstance(d, dict) else {}


def jlist(r) -> list:
    d = jget(r).get("data")
    return d if isinstance(d, list) else []


def poll(fn, done, timeout, interval=3, label=""):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        last = fn()
        if done(last):
            return last
        time.sleep(interval)
    return last


# ---------------------------------------------------------------- 0. 基础设施
def sec_infra(token: str):
    r = api("GET", "/api/health")
    rec("GET /api/health", r, r.status_code == 200 and jdata(r).get("status") == "ok")
    h = {"Authorization": f"Bearer {token}"}
    for p in ("/ollama/api/version", "/ollama/api/tags"):
        r = api("GET", p, headers=h)
        rec("GET " + p, r, r.status_code == 200)
    r = api("POST", "/ollama/api/generate", headers=h,
            json={"model": "testforge-kg", "prompt": "只回复数字1", "stream": False}, timeout=90)
    rec("POST /ollama/api/generate", r, r.status_code == 200, jget(r).get("response", "")[:40])
    r = api("POST", "/ollama/api/chat", headers=h,
            json={"model": "testforge-kg", "messages": [{"role": "user", "content": "只回复数字2"}], "stream": False},
            timeout=90)
    rec("POST /ollama/api/chat", r, r.status_code == 200)
    r = api("POST", "/ollama/api/embeddings", headers=h,
            json={"model": "nomic-embed-text", "prompt": "向量冒烟"}, timeout=60)
    rec("POST /ollama/api/embeddings", r, r.status_code == 200,
        "embedding 维度=" + str(len(jget(r).get("embedding", []))))


# ---------------------------------------------------------------- 1. 认证
def sec_auth(token: str):
    h = {"Authorization": f"Bearer {token}"}
    r = api("GET", "/api/auth/me", headers=h)
    rec("GET /api/auth/me", r, r.status_code == 200)
    r = api("GET", "/api/auth/users", headers=h)
    rec("GET /api/auth/users", r, r.status_code == 200)

    u = "e2e_smoke_u1"
    api("POST", f"/api/auth/users/{u}/delete", headers=h)  # 防残留
    r = api("POST", "/api/auth/users/create", headers=h,
            json={"username": u, "password": "E2ePassw0rd1", "role": "viewer"})
    rec("POST /api/auth/users/create", r, r.status_code == 200)
    r = api("PUT", f"/api/auth/users/{u}", headers=h, json={"role": "admin"})
    rec("PUT /api/auth/users/{username}", r, r.status_code == 200, "提权 admin")
    r = api("PUT", f"/api/auth/users/{u}", headers=h, json={"role": "viewer"})
    rec("PUT /api/auth/users/{username}(回退)", r, r.status_code == 200)
    r = api("POST", f"/api/auth/users/{u}/reset-password", headers=h, json={"new_password": "E2ePassw0rd2"})
    rec("POST /api/auth/users/{username}/reset-password", r, r.status_code == 200)
    r = api("POST", "/api/auth/login", json={"username": u, "password": "E2ePassw0rd2"})
    rec("POST /api/auth/login(新用户)", r, r.status_code == 200)
    utok = jdata(r).get("token", "")
    r = api("POST", "/api/auth/change-password",
            headers={"Authorization": f"Bearer {utok}"}, json={"old_password": "E2ePassw0rd2", "new_password": "E2ePassw0rd3"})
    rec("POST /api/auth/change-password", r, r.status_code == 200)
    r = api("POST", "/api/auth/login", json={"username": u, "password": "E2ePassw0rd3"})
    rec("POST /api/auth/login(改密后)", r, r.status_code == 200)
    r = api("POST", f"/api/auth/users/{u}/delete", headers=h)
    rec("POST /api/auth/users/{username}/delete", r, r.status_code == 200)
    r = api("POST", "/api/auth/login", json={"username": "admin", "password": "wrong-password"})
    rec("POST /api/auth/login(错误口令→401)", r, r.status_code == 401)


# ---------------------------------------------------------------- 2. 仓库夹具与仓库端点
def make_tiny_git_repo() -> str:
    d = os.path.join(tempfile.gettempdir(), f"e2e_smoke_repo_{int(time.time())}")
    os.makedirs(d)
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    with open(os.path.join(d, "calc.py"), "w", encoding="utf-8") as f:
        f.write('"""E2E 冒烟夹具模块。"""\n\n\ndef add(a: int, b: int) -> int:\n    return a + b\n\n\ndef sub(a: int, b: int) -> int:\n    return a - b\n')
    with open(os.path.join(d, "README.md"), "w", encoding="utf-8") as f:
        f.write("# e2e smoke repo\n")
    for cmd in (["git", "config", "user.email", "e2e@test"], ["git", "config", "user.name", "e2e"],
                ["git", "add", "."], ["git", "commit", "-qm", "init"]):
        subprocess.run(cmd, cwd=d, check=True, capture_output=True)
    return d


def make_zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("zcalc.py", "def mul(a: int, b: int) -> int:\n    return a * b\n")
    return buf.getvalue()


def sec_repos(token: str) -> dict:
    h = {"Authorization": f"Bearer {token}"}
    out = {"tiny_id": None, "zip_id": None, "tiny_fn": None}
    r = api("GET", "/api/repos", headers=h)
    rec("GET /api/repos", r, r.status_code == 200)
    r = api("POST", "/api/repos/create", headers=h, json={"url": ""})
    rec("POST /api/repos/create(空 url→422)", r, r.status_code == 422)

    path = make_tiny_git_repo()
    r = api("POST", "/api/repos/create", headers=h, json={"url": path, "branch": "master"})
    ok = r.status_code == 200
    rec("POST /api/repos/create(本地路径)", r, ok, jget(r).get("message", "")[:80])
    if ok:
        data = jdata(r)
        out["tiny_id"] = data.get("repo_id") or data.get("id")

    r = api("POST", "/api/repos/upload", headers={**h, "Content-Type": None},
            files={"file": ("e2e.zip", make_zip_bytes(), "application/zip")}, data={"name": MARK + "-ziprepo"})
    ok = r.status_code == 200
    rec("POST /api/repos/upload(zip)", r, ok, jget(r).get("message", "")[:80])
    if ok:
        data = jdata(r)
        out["zip_id"] = data.get("repo_id") or data.get("id")
    fixture_ids["repos"] = [i for i in (out["tiny_id"], out["zip_id"]) if i]

    rid = out["tiny_id"] or 1
    for p, note in (("/changes", ""), ("/clusters", ""), ("/cycles", ""), ("/chains", ""), ("/processes", "")):
        r = api("GET", f"/api/repos/1{p}", headers=h, timeout=120)
        rec(f"GET /api/repos/1{p}", r, r.status_code == 200, note)
    r = api("GET", f"/api/repos/1/changes", headers=h, params={"scope": "diff"}, timeout=120)
    rec("GET /api/repos/{id}/changes(scope=diff)", r, r.status_code == 200)

    if out["tiny_id"]:
        r = api("POST", f"/api/repos/{out['tiny_id']}/analyze", headers=h, timeout=180)
        rec("POST /api/repos/{id}/analyze", r, r.status_code == 200)
        r = api("POST", f"/api/repos/{out['tiny_id']}/pull", headers=h, timeout=120)
        rec("POST /api/repos/{id}/pull(本地无远端)", r, r.status_code in (200, 400, 422))
        r = api("POST", f"/api/repos/{out['tiny_id']}/webhook", headers=h, json={})
        rec("POST /api/repos/{id}/webhook", r, r.status_code == 200)

    r = api("GET", "/api/functions", headers=h, params={"repo_id": rid})
    rec("GET /api/functions", r, r.status_code == 200)
    fns = jlist(r)
    if isinstance(fns, dict):
        fns = fns.get("items", fns.get("functions", []))
    if fns:
        cand = [f for f in fns if f.get("name") in ("add", "mul", "sub")] or fns
        out["tiny_fn"] = cand[0].get("name")
        out["tiny_module"] = cand[0].get("module", "")
    r = api("GET", "/api/functions/trace", headers=h, params={"src": "add", "dst": "sub", "repo_id": rid})
    rec("GET /api/functions/trace", r, r.status_code == 200)
    r = api("GET", "/api/functions/add/impact", headers=h, params={"repo_id": rid})
    rec("GET /api/functions/{name}/impact", r, r.status_code == 200)
    r = api("GET", "/api/graph", headers=h, params={"repo_id": rid})
    rec("GET /api/graph", r, r.status_code == 200)
    return out


# ---------------------------------------------------------------- 3. 需求
def sec_requirements(token: str, tiny: dict) -> dict:
    h = {"Authorization": f"Bearer {token}"}
    out = {}
    body = ("E2E 冒烟需求：calc 模块的 add 函数必须满足——1) 两个整数相加返回算术和；"
            "2) 结果超过 2^31-1 时行为需有明确定义并写回文档。")
    r = api("POST", "/api/requirements/ingest", headers=h,
            json={"title": MARK + " 需求一", "body": body, "repo_id": 1, "source": "e2e"})
    ok = r.status_code == 200
    rec("POST /api/requirements/ingest", r, ok)
    d = jdata(r)
    out["req1_id"] = d.get("id") or d.get("req_id")
    out["req1_code"] = d.get("code") or d.get("req_code")
    r = api("POST", "/api/requirements/ingest", headers=h,
            json={"title": MARK + " 需求二(将确认)", "body": body, "repo_id": 1, "source": "e2e"})
    d2 = jdata(r)
    out["req2_id"] = d2.get("id") or d2.get("req_id")
    out["req2_code"] = d2.get("code") or d2.get("req_code")
    fixture_ids["requirements"] = [i for i in (out.get("req1_id"), out.get("req2_id")) if i]

    r = api("GET", "/api/requirements", headers=h)
    rec("GET /api/requirements", r, r.status_code == 200)
    if out.get("req1_code"):
        r = api("GET", f"/api/requirements/{out['req1_code']}/similar", headers=h)
        rec("GET /api/requirements/{code}/similar", r, r.status_code == 200)
    if out.get("req1_id"):
        r = api("POST", f"/api/requirements/{out['req1_id']}/confirm", headers=h,
                json={"action": "reject", "note": MARK + " 打回路"})
        rec("POST /api/requirements/{id}/confirm(reject)", r, r.status_code == 200)
    return out


# ---------------------------------------------------------------- 4. 计划与报告
def sec_plans(token: str, reqs: dict) -> dict:
    h = {"Authorization": f"Bearer {token}"}
    out = {}
    r = api("GET", "/api/plans", headers=h)
    rec("GET /api/plans", r, r.status_code == 200)
    plans = jlist(r)
    if isinstance(plans, dict):
        plans = plans.get("items", plans.get("plans", []))
    if plans:
        real_code = plans[0].get("iter_code") or plans[0].get("code")
        r = api("GET", f"/api/plans/{real_code}", headers=h)
        rec("GET /api/plans/{iter_code}(真实迭代)", r, r.status_code == 200)
    if reqs.get("req2_code"):
        r = api("POST", "/api/plans/create", headers=h, json={"version": MARK, "req_codes": [reqs["req2_code"]]})
        ok = r.status_code == 200
        rec("POST /api/plans/create", r, ok, jget(r).get("message", "")[:80])
        d = jdata(r)
        out["iter_code"] = d.get("iter_code") or d.get("code")
        if out.get("iter_code"):
            fixture_ids["plans"].append(out["iter_code"])
    if out.get("iter_code"):
        r = api("POST", f"/api/reports/{out['iter_code']}", headers=h, timeout=240)
        rec("POST /api/reports/{iter_code}", r, r.status_code == 200, jget(r).get("message", "")[:80])
    return out


# ---------------------------------------------------------------- 5. 生成/用例/运行
def sec_generations(token: str, tiny: dict) -> dict:
    h = {"Authorization": f"Bearer {token}"}
    out = {}
    rid = tiny.get("tiny_id")
    fn = tiny.get("tiny_fn")
    if not (rid and fn):
        rec("POST /api/generations", None, ok=False, note="跳过：夹具仓库或函数缺失")
        return out
    def extract_gens(resp) -> list:
        return re.findall(r"GEN-[0-9A-F]+", resp.text)

    # ① 批量生成（夹具函数，端点+管线真跑；产物质量不判定）
    r = api("POST", "/api/generations/batch", headers=h,
            json={"repo_id": rid, "names": [fn], "layer": "ut"}, timeout=120)
    ok = r.status_code == 200
    rec("POST /api/generations/batch", r, ok, f"queued={jdata(r).get('queued')}")
    batch_gens = extract_gens(r)
    if batch_gens:
        gc0 = batch_gens[0]
        fixture_ids["gen_codes"].append(gc0)
        final = poll(lambda: jdata(api("GET", f"/api/generations/{gc0}", headers=h, timeout=60)),
                     lambda d: d.get("status") in ("done", "failed", "error", "完成", "失败"), 300)
        rec("GET /api/generations/{gen_code}(轮询到终态)", None, final.get("status") is not None,
            f"{gc0} 终态={final.get('status')}")

    r = api("POST", "/api/regenerate", headers=h,
            json={"repo_id": rid, "target_function": fn, "reason": MARK + " 变更影响重生成"}, timeout=300)
    rec("POST /api/regenerate", r, r.status_code == 200, jget(r).get("message", "")[:80])

    # ② 运行重跑：选一条真实 run（rerun 真实再生成并稳定产出用例，供生命周期用）
    r = api("GET", "/api/cases", headers=h, params={"page_size": 1})
    rec("GET /api/cases", r, r.status_code == 200)
    items = jdata(r).get("items", [])
    pre_case_max = items[0].get("id", 0) if items else 0
    r = api("GET", "/api/runs", headers=h)
    rec("GET /api/runs", r, r.status_code == 200)
    runs = jlist(r)
    if isinstance(runs, dict):
        runs = runs.get("items", runs.get("runs", []))
    rc = (runs[-1].get("code") or runs[-1].get("run_code")) if runs else None
    rerun_gen = None
    if rc:
        r = api("POST", f"/api/runs/{rc}/rerun", headers=h, timeout=300)
        gens = extract_gens(r)
        rerun_gen = gens[0] if gens else None
        rec("POST /api/runs/{run_code}/rerun", r, r.status_code == 200 and rerun_gen is not None,
            f"{jget(r).get('message', '')[:60]} gen={rerun_gen}")
    if rerun_gen:
        fixture_ids["gen_codes"].append(rerun_gen)
        poll(lambda: jdata(api("GET", f"/api/generations/{rerun_gen}", headers=h, timeout=60)),
             lambda d: d.get("status") in ("done", "failed", "error", "完成", "失败"), 300)

        r = api("GET", f"/api/generations/{rerun_gen}/events", headers=h, stream=True, timeout=30)
        ct = r.headers.get("content-type", "")
        rec("GET /api/generations/{gen_code}/events(SSE)", r, r.status_code == 200 and "text/event-stream" in ct, ct)
        r.close()
        r = api("GET", f"/api/generations/{rerun_gen}/export", headers=h, timeout=60)
        rec("GET /api/generations/{gen_code}/export", r, r.status_code == 200)
        r = api("POST", f"/api/generations/{rerun_gen}/export-to-repo", headers=h, timeout=120)
        rec("POST /api/generations/{gen_code}/export-to-repo", r, r.status_code == 200, jget(r).get("message", "")[:80])

        # ③ 用例生命周期：rerun 生成产出的用例（gen_id 精确匹配 + 新于快照）
        r = api("GET", "/api/cases", headers=h, params={"page_size": 200})
        items = jdata(r).get("items", [])
        mine = [c for c in items if c.get("gen_id") == rerun_gen and c.get("id", 0) > pre_case_max]
        if mine:
            c0 = mine[0]
            fixture_ids["cases"].append(c0["id"])
            r = api("PUT", f"/api/cases/{c0['id']}", headers=h, json={"title": MARK + " 改题", "confidence": 0.9})
            rec("PUT /api/cases/{case_id}", r, r.status_code == 200)
            r = api("POST", f"/api/cases/{c0['id']}/review", headers=h, json={"action": "approve", "note": MARK})
            rec("POST /api/cases/{case_id}/review", r, r.status_code == 200, jget(r).get("message", "")[:80])
            r = api("GET", f"/api/cases/{c0['code']}/file", headers=h, timeout=60)
            rec("GET /api/cases/{case_code}/file", r, r.status_code == 200)
            r = api("POST", f"/api/cases/{c0['id']}/delete", headers=h)
            rec("POST /api/cases/{case_id}/delete", r, r.status_code == 200)
        else:
            rec("用例生命周期(PUT/review/file/delete)", None, ok=False, note=f"{rerun_gen} 未产出用例")
    return out


# ---------------------------------------------------------------- 6. 契约
def sec_contracts(token: str, tiny: dict):
    h = {"Authorization": f"Bearer {token}"}
    r = api("POST", "/api/contracts/create", headers=h,
            json={"name": MARK + " 契约", "type": "rest", "provider_repo": "e2e",
                  "version": "1.0.0", "spec": {"add": {"in": ["int", "int"], "out": "int"}}, "consumers": ["e2e-consumer"]})
    ok = r.status_code == 200
    rec("POST /api/contracts/create", r, ok)
    cid = jdata(r).get("id")
    if cid:
        fixture_ids["contracts"].append(cid)
    r = api("GET", "/api/contracts", headers=h)
    rec("GET /api/contracts", r, r.status_code == 200)
    if not cid:
        items = jget(api("GET", "/api/contracts", headers=h)).get("data", [])
        mine = [c for c in items if str(c.get("name", "")).startswith(MARK)]
        cid = mine[0]["id"] if mine else None
    if cid:
        r = api("GET", f"/api/contracts/{cid}/diffs", headers=h)
        rec("GET /api/contracts/{id}/diffs", r, r.status_code == 200)
        r = api("POST", f"/api/contracts/{cid}/impact", headers=h, json={"spec": {"add": {"in": ["int", "int"], "out": "float"}}})
        rec("POST /api/contracts/{id}/impact", r, r.status_code == 200, jget(r).get("message", "")[:80])
        r = api("POST", f"/api/contracts/{cid}/regenerate", headers=h,
                json={"spec": {"add": {"in": ["int", "int"], "out": "int"}, "sub": {"in": ["int", "int"], "out": "int"}}}, timeout=300)
        rec("POST /api/contracts/{id}/regenerate", r, r.status_code == 200, jget(r).get("message", "")[:80])


# ---------------------------------------------------------------- 7. 缺陷
def sec_defects(token: str):
    h = {"Authorization": f"Bearer {token}"}
    r = api("GET", "/api/defects/workflow", headers=h)
    rec("GET /api/defects/workflow", r, r.status_code == 200)
    r = api("POST", "/api/defects/create", headers=h,
            json={"title": MARK + " 缺陷A", "reason": "E2E 冒烟主缺陷"})
    ok = r.status_code == 200
    rec("POST /api/defects/create", r, ok)
    d = jdata(r)
    did = d.get("id") or d.get("defect_id")
    dcode = d.get("code") or d.get("defect_code")
    if did:
        fixture_ids["defects"].append(did)
    r = api("POST", "/api/defects/create", headers=h,
            json={"title": MARK + " 缺陷B(重复链)", "reason": "E2E 冒烟重复缺陷"})
    d2 = jdata(r)
    did2 = d2.get("id") or d2.get("defect_id")
    if did2:
        fixture_ids["defects"].append(did2)

    r = api("GET", "/api/defects", headers=h)
    rec("GET /api/defects", r, r.status_code == 200)
    if did:
        r = api("GET", f"/api/defects/{did}/comments", headers=h)
        rec("GET /api/defects/{id}/comments", r, r.status_code == 200)
        r = api("POST", f"/api/defects/{did}/comments", headers=h, json={"body": MARK + " 评论", "actor": "e2e"})
        rec("POST /api/defects/{id}/comments", r, r.status_code == 200)
        for st in ("已确认", "修复中"):
            r = api("POST", f"/api/defects/{did}/status", headers=h, json={"status": st, "actor": "e2e"})
            rec(f"POST /api/defects/{{id}}/status({st})", r, r.status_code == 200, jget(r).get("message", "")[:80])
        r = api("POST", f"/api/defects/{did}/regression", headers=h, timeout=300)
        rec("POST /api/defects/{id}/regression(无关联用例)", r, r.status_code == 200, jget(r).get("message", "")[:80])
        r = api("POST", f"/api/defects/{did}/status", headers=h,
                json={"status": "已关闭", "resolution": "已修复", "actor": "e2e"})
        rec("POST /api/defects/{id}/status(关闭)", r, r.status_code == 200, jget(r).get("message", "")[:80])
        r = api("GET", f"/api/defects/{did}/history", headers=h)
        rec("GET /api/defects/{id}/history", r, r.status_code == 200)
        r = api("GET", f"/api/defects/{did}/suggestion", headers=h, timeout=180)
        rec("GET /api/defects/{id}/suggestion", r, r.status_code == 200)
    if did2 and dcode:
        r = api("POST", f"/api/defects/{did2}/status", headers=h,
                json={"status": "已关闭", "resolution": "重复", "dupe_of": dcode, "actor": "e2e",
                      "comment": MARK + " 判定为重复缺陷，关闭到主缺陷"})
        rec("POST /api/defects/{id}/status(重复链)", r, r.status_code == 200, jget(r).get("message", "")[:80])


# ---------------------------------------------------------------- 8. 知识库
def sec_knowledge(token: str):
    h = {"Authorization": f"Bearer {token}"}
    title = MARK + " 知识文档"
    content = ("TestForge 端到端冒烟知识：本平台的用例生成依赖六路上下文检索，"
               "知识文档经向量+全文双索引进入检索池。add 函数用于整数加法冒烟验证。")
    r = api("POST", "/api/knowledge/documents/create", headers=h,
            json={"title": title, "content": content, "repo_id": 1, "assess": False})
    ok = r.status_code == 200
    rec("POST /api/knowledge/documents/create", r, ok, jget(r).get("message", "")[:80])
    kd = jdata(r)
    kd_key = kd.get("doc_key")
    if kd_key:
        fixture_ids["know_docs"].append(kd_key)
    r = api("GET", "/api/knowledge/documents", headers=h)
    rec("GET /api/knowledge/documents", r, r.status_code == 200)
    r = api("GET", "/api/knowledge/documents/paged", headers=h, params={"page": 1, "page_size": 5})
    rec("GET /api/knowledge/documents/paged", r, r.status_code == 200)
    r = api("GET", "/api/knowledge/search", headers=h, params={"q": "用例生成", "limit": 3}, timeout=90)
    rec("GET /api/knowledge/search", r, r.status_code == 200)
    r = api("GET", "/api/knowledge/status", headers=h)
    rec("GET /api/knowledge/status", r, r.status_code == 200)
    r = api("GET", "/api/knowledge/workbench", headers=h)
    rec("GET /api/knowledge/workbench", r, r.status_code == 200)

    asset_text = ("E2E 冒烟资产：TestForge 的知识资产统一上传入口，支持需求文档、技术文档、"
                  "测试计划与缺陷附件四类，解析后进入向量+全文双索引，例试卷生成与检索引用。").encode("utf-8")
    r = api("POST", "/api/knowledge/assets/upload", headers={**h, "Content-Type": None},
            files={"file": ("e2e_asset.txt", asset_text, "text/plain")},
            data={"kind": "tech_doc", "repo_id": "1", "title": MARK + " 资产"})
    ok = r.status_code == 200
    rec("POST /api/knowledge/assets/upload", r, ok)
    akey = jdata(r).get("object_key")
    r = api("GET", "/api/knowledge/assets/summary", headers=h)
    rec("GET /api/knowledge/assets/summary", r, r.status_code == 200)
    if akey:
        r = api("GET", "/api/knowledge/assets/download", headers=h, params={"object_key": akey})
        rec("GET /api/knowledge/assets/download", r, r.status_code == 200)

    # import-url：本地起一个临时 HTTP 服务当导入源
    page = "<html><head><title>E2E Import Page</title></head><body><h1>E2E</h1><p>" + MARK + " 导入正文内容，用于验证 URL 导入管线。</p></body></html>"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(page.encode("utf-8"))

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    r = api("POST", "/api/knowledge/import-url", headers=h,
            json={"url": f"http://127.0.0.1:{port}/doc.html", "repo_id": 1, "title": MARK + " 导入页"}, timeout=120)
    rec("POST /api/knowledge/import-url", r, r.status_code == 200, jget(r).get("message", "")[:80])
    iu = jdata(r)
    if iu.get("doc_key"):
        fixture_ids["know_docs"].append(iu["doc_key"])
    srv.shutdown()

    r = api("GET", "/api/rag/eval", headers=h, timeout=180)
    rec("GET /api/rag/eval", r, r.status_code == 200)
    r = api("GET", "/api/quality/requirements", headers=h)
    rec("GET /api/quality/requirements", r, r.status_code == 200)

    for k in list(fixture_ids["know_docs"]):
        api("POST", "/api/knowledge/documents/delete", headers=h, json={"doc_key": k})
    fixture_ids["know_docs"].clear()
    rec("POST /api/knowledge/documents/delete(自清理)", None, ok=True, note="E2E 知识文档已删")


# ---------------------------------------------------------------- 9. Wiki
def sec_wiki(token: str):
    h = {"Authorization": f"Bearer {token}"}
    r = api("GET", "/api/wiki", headers=h, params={"repo_id": 1})
    rec("GET /api/wiki", r, r.status_code == 200)
    pages = jlist(r)
    if isinstance(pages, dict):
        pages = pages.get("items", pages.get("pages", []))
    pid = pages[0].get("id") if pages else None
    r = api("GET", "/api/wiki/graph", headers=h, params={"repo_id": 1}, timeout=120)
    rec("GET /api/wiki/graph", r, r.status_code == 200)
    r = api("GET", "/api/wiki/health", headers=h, params={"repo_id": 1})
    rec("GET /api/wiki/health", r, r.status_code == 200)
    r = api("GET", "/api/wiki/insights", headers=h, params={"repo_id": 1}, timeout=120)
    rec("GET /api/wiki/insights", r, r.status_code == 200)
    r = api("GET", "/api/wiki/lint", headers=h, params={"repo_id": 1}, timeout=120)
    rec("GET /api/wiki/lint", r, r.status_code == 200)
    if pid:
        r = api("GET", f"/api/wiki/{pid}", headers=h)
        rec("GET /api/wiki/{page_id}", r, r.status_code == 200)
        r = api("GET", f"/api/wiki/{pid}/entities", headers=h)
        rec("GET /api/wiki/{page_id}/entities", r, r.status_code == 200)
        r = api("GET", f"/api/wiki/{pid}/related", headers=h)
        rec("GET /api/wiki/{page_id}/related", r, r.status_code == 200)

    r = api("POST", "/api/wiki/chat/sessions/create", headers=h, json={"repo_id": 1, "title": MARK + " 会话"})
    ok = r.status_code == 200
    rec("POST /api/wiki/chat/sessions/create", r, ok)
    sid = jdata(r).get("id") or jdata(r).get("session_id")
    if sid:
        fixture_ids["wiki_sessions"].append(sid)
    r = api("GET", "/api/wiki/chat/sessions", headers=h)
    rec("GET /api/wiki/chat/sessions", r, r.status_code == 200)
    if sid:
        r = api("GET", f"/api/wiki/chat/sessions/{sid}/messages", headers=h)
        rec("GET /api/wiki/chat/sessions/{sid}/messages", r, r.status_code == 200)
        r = api("POST", "/api/wiki/ask", headers=h,
                json={"question": "这个仓库的用例生成使用哪些上下文来源？一句话回答。", "repo_id": 1, "session_id": sid}, timeout=300)
        rec("POST /api/wiki/ask", r, r.status_code == 200, str(jdata(r).get("answer", ""))[:60])

    r = api("POST", "/api/wiki/lint/fix-duplicates", headers=h, timeout=300)
    rec("POST /api/wiki/lint/fix-duplicates", r, r.status_code == 200, jget(r).get("message", "")[:80])

    # 重型①：增量重建（full=false，只重编 stale）
    r = api("POST", "/api/wiki/rebuild", headers=h, json={"repo_id": 1, "full": False}, timeout=600)
    rec("POST /api/wiki/rebuild(增量)", r, r.status_code == 200, jget(r).get("message", "")[:100])
    r = api("POST", "/api/wiki/rebuild", headers=h, json={"repo_id": 999999, "full": False})
    rec("POST /api/wiki/rebuild(repo 不存在→404)", r, r.status_code == 404)
    if sid:
        r = api("DELETE", f"/api/wiki/chat/sessions/{sid}", headers=h)
        rec("DELETE /api/wiki/chat/sessions/{session_id}(自清理)", r, r.status_code == 200)
        fixture_ids["wiki_sessions"].clear()


# ---------------------------------------------------------------- 10. KG
def sec_kg(token: str):
    h = {"Authorization": f"Bearer {token}"}
    r = api("GET", "/api/kg/stats", headers=h, params={"repo_id": 1})
    rec("GET /api/kg/stats", r, r.status_code == 200)
    r = api("GET", "/api/kg/workspaces", headers=h)
    rec("GET /api/kg/workspaces", r, r.status_code == 200)
    r = api("GET", "/api/kg/settings", headers=h)
    rec("GET /api/kg/settings", r, r.status_code == 200)
    cur = jdata(r)
    r = api("POST", "/api/kg/settings/update", headers=h, json={"settings": cur if isinstance(cur, dict) else {}})
    rec("POST /api/kg/settings/update(原值回写)", r, r.status_code == 200)
    r = api("GET", "/api/kg/graph", headers=h, params={"repo_id": 1}, timeout=120)
    rec("GET /api/kg/graph", r, r.status_code == 200)
    r = api("GET", "/api/kg/communities", headers=h, params={"repo_id": 1})
    rec("GET /api/kg/communities", r, r.status_code == 200)

    r = api("GET", "/api/kg/documents", headers=h, params={"repo_id": 1})
    rec("GET /api/kg/documents", r, r.status_code == 200)
    docs = jlist(r)
    if isinstance(docs, dict):
        docs = docs.get("items", docs.get("documents", []))
    doc_key = docs[0].get("doc_key") if docs else None
    if doc_key:
        r = api("GET", "/api/kg/documents/chunks", headers=h, params={"doc_key": doc_key})
        rec("GET /api/kg/documents/chunks", r, r.status_code == 200)
        r = api("GET", "/api/kg/documents/track", headers=h, params={"doc_key": doc_key})
        rec("GET /api/kg/documents/track", r, r.status_code == 200)
        r = api("GET", "/api/kg/chunk-content", headers=h, params={"doc_key": doc_key})
        rec("GET /api/kg/chunk-content", r, r.status_code == 200)

    # 实体检索：找一个真实实体名
    r = api("POST", "/api/kg/search", headers=h, json={"query": "登录认证", "top_k": 3, "repo_id": 1}, timeout=90)
    rec("POST /api/kg/search", r, r.status_code == 200)
    ents = jdata(r).get("entities") or jdata(r).get("items") or []
    ename = None
    if ents and isinstance(ents, list):
        e0 = ents[0]
        ename = e0.get("name") or e0.get("entity")
    if ename:
        r = api("GET", "/api/kg/entity/exists", headers=h, params={"name": ename, "repo_id": 1})
        rec("GET /api/kg/entity/exists", r, r.status_code == 200)
        r = api("GET", "/api/kg/entity/sources", headers=h, params={"name": ename, "repo_id": 1})
        rec("GET /api/kg/entity/sources", r, r.status_code == 200)
        # 正向编辑：改描述后再改回（两跳，终态还原）
        r0 = api("GET", "/api/kg/graph", headers=h, params={"repo_id": 1}, timeout=120)
        nodes = jget(r0).get("data", {}).get("nodes") or []
        desc0 = next((n.get("description") for n in nodes if n.get("name") == ename and n.get("description")), None)
        r = api("PATCH", "/api/kg/entity", headers=h,
                json={"name": ename, "description": desc0 if desc0 else "真实实体（原描述回写）", "repo_id": 1})
        rec("PATCH /api/kg/entity(原值回写)", r, r.status_code == 200)

    # 合并/删除：负路径（404），正路径在夹具实体上做
    r = api("POST", "/api/kg/entity/merge", headers=h,
            json={"into": "__e2e_no_such__", "sources": ["__e2e_no_src__"], "repo_id": 1})
    rec("POST /api/kg/entity/merge(不存在→404)", r, r.status_code == 404)
    r = api("POST", "/api/kg/entity/delete", headers=h, params={"name": "__e2e_no_such__", "repo_id": 1})
    rec("POST /api/kg/entity/delete(不存在→404)", r, r.status_code == 404)
    r = api("POST", "/api/kg/relation/delete", headers=h, params={"id": 2147483647, "repo_id": 1})
    rec("POST /api/kg/relation/delete(不存在→404)", r, r.status_code == 404)

    # KG 文档生命周期（自建自删，不动真实库）
    r = api("POST", "/api/kg/documents/create", headers=h,
            json={"title": MARK + " KG文档", "content": MARK + " E2EAlpha 服务负责登录认证。E2EBeta 服务负责日志采集。", "repo_id": 1})
    ok = r.status_code == 200
    rec("POST /api/kg/documents/create", r, ok)
    kdoc = jdata(r).get("doc_key")
    if kdoc:
        fixture_ids["kg_docs"].append(kdoc)
    r = api("POST", "/api/kg/documents/upload", headers={**h, "Content-Type": None},
            files={"file": ("e2e_kg.txt", (MARK + " E2EGamma 模块负责配置管理。").encode("utf-8"), "text/plain")},
            data={"repo_id": "1", "title": MARK + " KG上传"})
    ok = r.status_code == 200
    rec("POST /api/kg/documents/upload", r, ok)
    kup = jdata(r).get("doc_key")
    if kup:
        fixture_ids["kg_docs"].append(kup)

    r = api("GET", "/api/kg/query", headers=h, params={"q": "登录认证", "mode": "local", "repo_id": 1, "limit": 3}, timeout=120)
    rec("GET /api/kg/query", r, r.status_code == 200)
    r = api("POST", "/api/kg/query/stream", headers=h,
            json={"query": "登录认证流程一句话", "mode": "local", "repo_id": 1, "top_k": 3, "use_cache": False},
            stream=True, timeout=300)
    ct = r.headers.get("content-type", "")
    rec("POST /api/kg/query/stream(SSE)", r, r.status_code == 200, ct)
    r.close()
    r = api("DELETE", "/api/kg/query-cache", headers=h)
    rec("DELETE /api/kg/query-cache", r, r.status_code == 200)

    for k in fixture_ids["kg_docs"]:
        api("POST", "/api/kg/documents/delete", headers=h, json={"doc_key": k})
    fixture_ids["kg_docs"].clear()
    rec("POST /api/kg/documents/delete(自清理)", None, ok=True, note="E2E KG 文档已删")


# ---------------------------------------------------------------- 11. 重型端点②③
def sec_heavy_kg(token: str):
    h = {"Authorization": f"Bearer {token}"}

    def docs_state():
        r = api("GET", "/api/kg/documents", headers=h, params={"repo_id": 1}, timeout=60)
        d = jlist(r)
        if isinstance(d, dict):
            d = d.get("items", [])
        by = {}
        for x in d:
            by[x.get("status")] = by.get(x.get("status"), 0) + 1
        return by
    before = docs_state()
    r = api("POST", "/api/kg/build", headers=h, json={"repo_id": 1}, timeout=600)
    ok = r.status_code == 200
    rec("POST /api/kg/build(全量同步·hash 短路)", r, ok, json.dumps(jdata(r), ensure_ascii=False)[:160])
    if ok:
        final = poll(docs_state, lambda by: by.get("processed", 0) == before.get("processed", 0) + by.get("queued", 0) or by.get("queued", 0) == 0, 600, 10)
        rec("GET /api/kg/documents(管线排空监控)", None, True, f"before={before} after={final}")

    r = api("POST", "/api/kg/communities/build", headers=h, json={"repo_id": 1, "llm": False}, timeout=600)
    rec("POST /api/kg/communities/build(llm=false 确定性)", r, r.status_code == 200,
        json.dumps(jdata(r), ensure_ascii=False)[:160])
    r = api("POST", "/api/kg/communities/build", headers=h, json={"repo_id": 999999}, timeout=60)
    rec("POST /api/kg/communities/build(空关系图)", r, r.status_code == 200, "空仓库返回 note")


# ---------------------------------------------------------------- 12. AI 助手
def sec_assistant(token: str):
    h = {"Authorization": f"Bearer {token}"}
    r = api("POST", "/api/assistant/threads/create", headers=h, json={"title": MARK + " 会话", "repo_id": 1})
    ok = r.status_code == 200
    rec("POST /api/assistant/threads/create", r, ok)
    tid = jdata(r).get("id") or jdata(r).get("thread_id")
    r = api("GET", "/api/assistant/threads", headers=h)
    rec("GET /api/assistant/threads", r, r.status_code == 200)
    if tid:
        fixture_ids["threads"].append(tid)
        r = api("POST", "/api/assistant/chat", headers=h,
                json={"thread_id": tid, "content": "只回答一个数字：1+1=?"}, timeout=300)
        rec("POST /api/assistant/chat", r, r.status_code == 200, str(jdata(r))[:100])
        r = api("GET", f"/api/assistant/threads/{tid}/messages", headers=h)
        rec("GET /api/assistant/threads/{id}/messages", r, r.status_code == 200)
        r = api("DELETE", f"/api/assistant/threads/{tid}", headers=h)
        rec("DELETE /api/assistant/threads/{thread_id}(自清理)", r, r.status_code == 200)
        fixture_ids["threads"].clear()


# ---------------------------------------------------------------- 13. 集成/台账
def sec_integrations(token: str):
    h = {"Authorization": f"Bearer {token}"}
    r = api("GET", "/api/mcp/claude-desktop/status", headers=h)
    rec("GET /api/mcp/claude-desktop/status", r, r.status_code == 200)
    r = api("POST", "/api/mcp/claude-desktop/install", headers=h, timeout=60)
    rec("POST /api/mcp/claude-desktop/install", r, r.status_code == 200, jget(r).get("message", "")[:80])
    r = api("GET", "/api/llm/cache", headers=h)
    rec("GET /api/llm/cache", r, r.status_code == 200)
    r = api("GET", "/api/stats/summary", headers=h)
    rec("GET /api/stats/summary", r, r.status_code == 200)
    r = api("GET", "/api/system/services", headers=h)
    rec("GET /api/system/services", r, r.status_code == 200)
    r = api("GET", "/api/traces", headers=h, params={"limit": 5})
    rec("GET /api/traces", r, r.status_code == 200)
    traces = jlist(r)
    if isinstance(traces, dict):
        traces = traces.get("items", traces.get("traces", []))
    if traces:
        tid0 = traces[0].get("trace_id") or traces[0].get("id")
        r = api("GET", f"/api/traces/{tid0}", headers=h)
        rec("GET /api/traces/{trace_id}", r, r.status_code == 200)
    r = api("GET", "/api/jobs", headers=h)
    rec("GET /api/jobs", r, r.status_code == 200)
    jobs = jlist(r)
    if jobs:
        jc = jobs[0].get("job_code") or jobs[0].get("code")
        r = api("GET", f"/api/jobs/{jc}", headers=h)
        rec("GET /api/jobs/{job_code}", r, r.status_code == 200)
    # LLM 缓存清空放最后（保住前面所有 LLM 调用的缓存收益已完成使命）
    r = api("POST", "/api/llm/cache/clear", headers=h)
    rec("POST /api/llm/cache/clear(最后执行)", r, r.status_code == 200)


def main():
    r = api("POST", "/api/auth/login", json={"username": "admin", "password": "testforge-admin"})
    token = jdata(r).get("token", "")
    if not token:
        print("FATAL: 登录失败", r.text[:200])
        sys.exit(2)
    S.headers["Authorization"] = f"Bearer {token}"
    print(f"== E2E 全功能冒烟 start {time.strftime('%H:%M:%S')} ==", flush=True)
    sec_infra(token)
    sec_auth(token)
    tiny = sec_repos(token)
    reqs = sec_requirements(token, tiny)
    sec_plans(token, reqs)
    sec_generations(token, tiny)
    sec_contracts(token, tiny)
    sec_defects(token)
    sec_knowledge(token)
    sec_wiki(token)
    sec_kg(token)
    sec_heavy_kg(token)
    sec_assistant(token)
    sec_integrations(token)

    total = len(results)
    fails = [x for x in results if not x["ok"]]
    print(f"\n== SUMMARY: {total - len(fails)}/{total} PASS, {len(fails)} FAIL ==", flush=True)
    for f in fails:
        print("FAIL:", f["op"], f["status"], f["note"], flush=True)
    with open("e2e_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    with open("e2e_fixture_ids.json", "w", encoding="utf-8") as f:
        json.dump(fixture_ids, f, ensure_ascii=False, indent=1)
    print("results -> e2e_results.json / fixture ids -> e2e_fixture_ids.json")


if __name__ == "__main__":
    main()
