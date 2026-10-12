# -*- coding: utf-8 -*-
"""清理本会话（2026-10-12）门禁 fuzz 垃圾 + E2E 冒烟夹具。

用法：python e2e_cleanup.py inventory  # 只盘点
      python e2e_cleanup.py execute   # 实删（FK 安全顺序）
"""
from __future__ import annotations

import shutil
import sys
from datetime import datetime

from sqlalchemy import text

from app.db.session import get_session
from app.models import (Cases, ChatThread, ContractDiffs, Contracts, DefectActivity,
                        DefectComments, Defects, Generations, Iterations,
                        Jobs, Repos, Requirements, Runs, Users, WikiChatMessage,
                        WikiChatSession)

T0 = datetime(2026, 10, 12, 12, 20)  # 本会话 pytest+门禁+E2E 起点
MODE = sys.argv[1] if len(sys.argv) > 1 else "inventory"


def rows(s, sql, **params):
    return s.execute(text(sql), params).all()


def one(s, sql, **params):
    return rows(s, sql, **params)[0][0]


def main() -> None:
    with get_session() as s:
        # ---------- 1. 仓库（门禁 url 0/00 + E2E 夹具仓库） ----------
        junk_repos = rows(s, "SELECT id, url, status FROM repos WHERE url IN ('0','00')"
                             " OR url LIKE '%e2e_smoke_repo%' OR url LIKE 'upload://E2E-SMOKE%'")
        junk_repo_ids = [r[0] for r in junk_repos]
        print(f"repos 待删 {len(junk_repo_ids)}: {junk_repo_ids}")

        # ---------- 2. 需求（E2E + 门禁 title='0'） ----------
        junk_reqs = rows(s, "SELECT id, code, title FROM requirements WHERE title LIKE 'E2E-SMOKE%' OR title = '0'")
        junk_req_ids = [r[0] for r in junk_reqs]
        print(f"requirements 待删 {len(junk_req_ids)}: {[(r[1], r[2]) for r in junk_reqs][:6]} ...")

        # ---------- 3. 缺陷（E2E + 门禁新增；真实历史缺陷不动） ----------
        junk_defects = rows(s, "SELECT id, title FROM defects WHERE title LIKE 'E2E-SMOKE%'"
                               " OR (created_at > :t AND (title = '0' OR title LIKE '回归:%' OR title = '人工报障'))", t=T0)
        junk_defect_ids = [r[0] for r in junk_defects]
        print(f"defects 待删 {len(junk_defect_ids)}: {junk_defects[:5]} ...")
        gate_defects = rows(s, "SELECT count(*) FROM defects WHERE created_at > :t AND title NOT LIKE 'E2E-SMOKE%'"
                               " AND title <> '0' AND title NOT LIKE '回归:%'", t=T0)
        print(f"  [复核] 门禁窗口内其他新增缺陷 {gate_defects[0][0]} 条（默认保留，若为 fuzz 产物请人工过目）")

        # ---------- 4. 契约（E2E + 门禁 name 00/0） ----------
        junk_contracts = rows(s, "SELECT id, name FROM contracts WHERE name LIKE 'E2E-SMOKE%' OR name IN ('0','00')")
        junk_contract_ids = [r[0] for r in junk_contracts]
        print(f"contracts 待删 {len(junk_contract_ids)}: {junk_contracts}")

        # ---------- 5. 迭代（E2E version + 门禁窗口新增无 real version 的） ----------
        junk_iters = rows(s, "SELECT id, code, version FROM iterations WHERE version LIKE 'E2E-SMOKE%'"
                             " OR created_at > :t", t=T0)
        junk_iter_ids = [r[0] for r in junk_iters]
        real_iters = one(s, "SELECT count(*) FROM iterations WHERE created_at <= :t", t=T0)
        print(f"iterations 待删 {len(junk_iter_ids)}: {junk_iters[:8]} ...（窗口前真实迭代 {real_iters} 条保留）")

        # ---------- 6. 生成/运行/任务（窗口内的全部 = 门禁 fn0 + E2E add/rerun） ----------
        junk_gens = rows(s, "SELECT id, code, target_function, repo_id, status FROM generations WHERE created_at > :t", t=T0)
        junk_gen_ids = [r[0] for r in junk_gens]
        junk_gen_codes = [r[1] for r in junk_gens]
        print(f"generations 待删 {len(junk_gen_ids)}: {[(r[1], r[2], r[4]) for r in junk_gens]}")
        junk_runs = one(s, "SELECT count(*) FROM runs WHERE created_at > :t", t=T0)
        print(f"runs 待删 {junk_runs}")
        junk_jobs = one(s, "SELECT count(*) FROM jobs WHERE created_at > :t", t=T0)
        print(f"jobs 待删 {junk_jobs}")

        # ---------- 7. 会话/线程 ----------
        junk_wsess = rows(s, "SELECT id, title, repo_id FROM wiki_chat_sessions WHERE created_at > :t", t=T0)
        junk_wsess_ids = [r[0] for r in junk_wsess]
        print(f"wiki_chat_sessions 待删 {len(junk_wsess_ids)}: {junk_wsess[:6]} ...")
        junk_threads = rows(s, "SELECT id, title FROM chat_threads WHERE created_at > :t OR title LIKE 'E2E%'", t=T0)
        junk_thread_ids = [r[0] for r in junk_threads]
        print(f"chat_threads 待删 {len(junk_thread_ids)}: {junk_threads[:6]} ...")
        junk_users = rows(s, "SELECT username FROM users WHERE created_at > :t", t=T0)
        print(f"users 待删 {len(junk_users)}: {junk_users}")

        # ---------- 8. KG / 知识文档（键精确圈定） ----------
        kg_keys = [r[0] for r in rows(s, "SELECT doc_key FROM doc_status WHERE updated_at > :t AND workspace <> 'repo:1'", t=T0)]
        print(f"doc_status 垃圾键 {len(kg_keys)}: {kg_keys}")
        e2e_wiki_pages = [r[0] for r in rows(s, "SELECT id FROM wiki_pages WHERE repo_id = ANY(:ids)", ids=junk_repo_ids or [-1])]
        print(f"wiki_pages(E2E 仓库页) 待删 {len(e2e_wiki_pages)}: {e2e_wiki_pages}")
        rag_keys = [r[0] for r in rows(s, "SELECT doc_key FROM rag_documents WHERE (title LIKE 'E2E%' OR title = '0')"
                                          " AND kind <> 'kg_community'")]
        print(f"rag_documents(E2E/门禁标题) 待删 {len(rag_keys)}: {rag_keys[:12]} ...")

        # ---------- 9. 用例（rerun 产出的 sanitize 用例 = 窗口内 generations 的） ----------
        junk_case_cnt = one(s, "SELECT count(*) FROM cases WHERE gen_id = ANY(:codes)",
                            codes=junk_gen_codes or ["__none__"])
        print(f"cases 待删(gen 归属窗口生成) {junk_case_cnt}")

        if MODE != "execute":
            print("\n[inventory 模式，未删除任何数据]")
            return

        # ================= 实删（FK 安全顺序：子表先删） =================
        # 缺陷子表
        if junk_defect_ids:
            n = s.execute(text("DELETE FROM defect_comments WHERE defect_id = ANY(:ids)"), {"ids": junk_defect_ids}).rowcount
            print("删 defect_comments:", n)
            n = s.execute(text("DELETE FROM defect_activity WHERE defect_id = ANY(:ids)"), {"ids": junk_defect_ids}).rowcount
            print("删 defect_activity:", n)
            n = s.execute(text("DELETE FROM defects WHERE id = ANY(:ids)"), {"ids": junk_defect_ids}).rowcount
            print("删 defects:", n)
        # 契约子表
        if junk_contract_ids:
            n = s.execute(text("DELETE FROM contract_diffs WHERE contract_id = ANY(:ids)"), {"ids": junk_contract_ids}).rowcount
            print("删 contract_diffs:", n)
            n = s.execute(text("DELETE FROM contracts WHERE id = ANY(:ids)"), {"ids": junk_contract_ids}).rowcount
            print("删 contracts:", n)
        # 用例/生成/事件/任务/运行
        n = s.execute(text("DELETE FROM cases WHERE gen_id = ANY(:codes)"), {"codes": junk_gen_codes or ["__none__"]}).rowcount
        print("删 cases(rerun 产物):", n)
        n = s.execute(text("DELETE FROM generation_events WHERE gen_code = ANY(:codes)"), {"codes": junk_gen_codes or ["__none__"]}).rowcount
        print("删 generation_events:", n)
        n = s.execute(text("DELETE FROM generations WHERE id = ANY(:ids)"), {"ids": junk_gen_ids or [-1]}).rowcount
        print("删 generations:", n)
        n = s.execute(text("DELETE FROM jobs WHERE created_at > :t"), {"t": T0}).rowcount
        print("删 jobs:", n)
        n = s.execute(text("DELETE FROM runs WHERE created_at > :t"), {"t": T0}).rowcount
        print("删 runs:", n)
        # 需求
        n = s.execute(text("DELETE FROM requirements WHERE id = ANY(:ids)"), {"ids": junk_req_ids or [-1]}).rowcount
        print("删 requirements:", n)
        # 迭代
        n = s.execute(text("DELETE FROM iterations WHERE id = ANY(:ids)"), {"ids": junk_iter_ids or [-1]}).rowcount
        print("删 iterations:", n)
        # wiki 会话
        if junk_wsess_ids:
            n = s.execute(text("DELETE FROM wiki_chat_messages WHERE session_id = ANY(:ids)"), {"ids": junk_wsess_ids}).rowcount
            print("删 wiki_chat_messages:", n)
            n = s.execute(text("DELETE FROM wiki_chat_sessions WHERE id = ANY(:ids)"), {"ids": junk_wsess_ids}).rowcount
            print("删 wiki_chat_sessions:", n)
        # 助手线程
        if junk_thread_ids:
            n = s.execute(text("DELETE FROM chat_messages WHERE thread_id = ANY(:ids)"), {"ids": junk_thread_ids}).rowcount
            print("删 chat_messages:", n)
            n = s.execute(text("DELETE FROM chat_threads WHERE id = ANY(:ids)"), {"ids": junk_thread_ids}).rowcount
            print("删 chat_threads:", n)
        # 用户
        n = s.execute(text("DELETE FROM users WHERE username = ANY(:names)"),
                      {"names": [r[0] for r in junk_users] or ["__none__"]}).rowcount
        print("删 users:", n)
        # E2E 仓库的 wiki 页 + 依赖（必须先于 repos 删，有 FK）
        if e2e_wiki_pages:
            try:
                n = s.execute(text("DELETE FROM wiki_deps WHERE page_id = ANY(:ids)"), {"ids": e2e_wiki_pages}).rowcount
                print("删 wiki_deps:", n)
            except Exception as e:  # noqa: BLE001
                print("wiki_deps 跳过:", str(e)[:80])
            n = s.execute(text("DELETE FROM wiki_pages WHERE id = ANY(:ids)"), {"ids": e2e_wiki_pages}).rowcount
            print("删 wiki_pages:", n)
        # 仓库（最后删，其上 functions/cases/聚类等已随上面清掉；repo 级残留一并清）
        if junk_repo_ids:
            for tbl in ("fn_impacts", "fn_clusters"):
                n = s.execute(text(f"DELETE FROM {tbl} WHERE repo_id = ANY(:ids)"), {"ids": junk_repo_ids}).rowcount
                print(f"删 {tbl}:", n)
            n = s.execute(text("DELETE FROM call_edges WHERE caller_id IN (SELECT id FROM functions WHERE repo_id = ANY(:ids))"
                               " OR callee_id IN (SELECT id FROM functions WHERE repo_id = ANY(:ids))"), {"ids": junk_repo_ids}).rowcount
            print("删 call_edges:", n)
            n = s.execute(text("DELETE FROM functions WHERE repo_id = ANY(:ids)"), {"ids": junk_repo_ids}).rowcount
            print("删 functions:", n)
            n = s.execute(text("DELETE FROM repos WHERE id = ANY(:ids)"), {"ids": junk_repo_ids}).rowcount
            print("删 repos:", n)
        # rag 投影/残留（kg_entity/kg_relation 投影 + E2E wiki 页 + 门禁标题）
        n = 0
        for r in rows(s, "SELECT doc_key FROM rag_documents WHERE (title LIKE 'E2E%' OR title = '0') AND kind <> 'kg_community'"):
            n += s.execute(text("DELETE FROM rag_documents WHERE doc_key = :k"), {"k": r[0]}).rowcount
        for rid in junk_repo_ids:
            n += s.execute(text("DELETE FROM rag_documents WHERE doc_key LIKE :p"), {"p": f"kg_entity:{rid}:%"}).rowcount
            n += s.execute(text("DELETE FROM rag_documents WHERE doc_key LIKE :p"), {"p": f"kg_relation:{rid}:%"}).rowcount
        print("删 rag_documents(E2E/门禁/投影):", n)
        # doc_status 垃圾键
        n = 0
        for k in kg_keys:
            n += s.execute(text("DELETE FROM doc_status WHERE doc_key = :k"), {"k": k}).rowcount
        print("删 doc_status:", n)
        s.commit()
        print("DB 清理完成（KG 图谱侧走 API 级联，见下）")

    # ---------- KG 图谱侧走 API（级联删实体/关系/投影源） ----------
    import json
    import urllib.request
    BASE = "http://127.0.0.1:8000"
    S = {"Content-Type": "application/json"}

    def call(method, path, **kw):
        req = urllib.request.Request(BASE + path, method=method, **kw)
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read() or b"{}")

    _, d = call("POST", "/api/auth/login", data=json.dumps({"username": "admin", "password": "testforge-admin"}).encode(), headers=S)
    S["Authorization"] = f"Bearer {d['data']['token']}"
    H = S
    with get_session() as s:
        kg_keys = [r[0] for r in s.execute(text(
            "SELECT doc_key FROM doc_status WHERE updated_at > :t AND workspace <> 'repo:1'"), {"t": T0}).all()]
        e2e_ents = [r[0] for r in s.execute(text(
            "SELECT name FROM kg_entities WHERE workspace = 'repo:1' AND repo_id = 1 AND name LIKE 'E2E%'")).all()]
    for k in kg_keys:
        ep = "/api/knowledge/documents/delete" if k.startswith("userdoc:") else "/api/kg/documents/delete"
        try:
            st, _ = call("POST", ep, data=json.dumps({"doc_key": k}).encode(), headers=H)
            print("API 删文档", k, st)
        except Exception as e:  # noqa: BLE001
            print("API 删文档失败", k, str(e)[:100])
    # repo:1 里被 E2E 文档抽出来的实体（连带关系边级联）
    from app.db.session import get_session as _gs
    for name in e2e_ents:
        try:
            st, _ = call("POST", f"/api/kg/entity/delete?name={urllib.request.quote(name)}&repo_id=1&workspace=re%3A1", headers=H)
            print("API 删 E2E 实体", name, st)
        except Exception as e:  # noqa: BLE001
            print("API 删实体失败", name, str(e)[:100])
    # 清掉这些实体的 rag 投影
    with _gs() as s:
        n = s.execute(text("DELETE FROM rag_documents WHERE doc_key LIKE 'kg_entity:1:repo:1:E2E%'"
                           " OR doc_key LIKE 'kg_relation:1:repo:1:E2E%'")).rowcount
        s.commit()
    print("删 rag 投影(repo:1 E2E 实体/关系):", n)

    # ---------- 磁盘：E2E 仓库克隆目录 ----------
    import os
    import tempfile
    for name in os.listdir(tempfile.gettempdir()):
        if name.startswith("e2e_smoke_repo"):
            p = os.path.join(tempfile.gettempdir(), name)
            shutil.rmtree(p, ignore_errors=True)
            print("删磁盘目录:", p)


if __name__ == "__main__":
    main()
