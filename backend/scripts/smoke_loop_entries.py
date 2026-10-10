# -*- coding: utf-8 -*-
"""知识闭环两入口端到端冒烟（打真实网关 :8000）：上传代码包 / 知识资产 / 历史缺陷导入 → 检索验证 → 清理。"""
import csv
import io
import json
import tempfile
import time
import urllib.request
import uuid
import zipfile
from pathlib import Path

BASE = "http://127.0.0.1:8000"
SLUG = "smoke-kp-" + uuid.uuid4().hex[:6]


def call(method: str, path: str, token: str = "", data: bytes | None = None, headers: dict | None = None, form_boundary: str = ""):
    h = {"Authorization": f"Bearer {token}"} if token else {}
    if headers:
        h.update(headers)
    if form_boundary:
        h["Content-Type"] = f"multipart/form-data; boundary={form_boundary}"
    req = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=300) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def multipart(fields: dict, file_field: tuple[str, str, bytes], boundary: str) -> bytes:
    buf = io.BytesIO()
    for k, v in fields.items():
        buf.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8"))
    name, filename, content = file_field
    buf.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode("utf-8"))
    buf.write(content)
    buf.write(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return buf.getvalue()


def main() -> None:
    ok = 0
    # 登录
    req = urllib.request.Request(BASE + "/api/auth/login", data=json.dumps({"username": "admin", "password": "testforge-admin"}).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        token = json.loads(resp.read())["data"]["token"]
    print("1 登录 ok")

    # 入口①：小 python 项目 zip
    tmp = Path(tempfile.mkdtemp())
    proj = tmp / f"{SLUG}-main" / "src"
    proj.mkdir(parents=True)
    (proj / "cart.py").write_text(
        "def total(price: int, qty: int) -> int:\n    return price * qty\n\n\ndef checkout(price: int, qty: int) -> int:\n    return total(price, qty)\n",
        encoding="utf-8",
    )
    zpath = tmp / f"{SLUG}.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        for f in proj.parent.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(tmp))
    b = str(uuid.uuid4().hex)
    status, body = call("POST", "/api/repos/upload", token, data=multipart({"name": SLUG}, ("file", zpath.name, zpath.read_bytes()), b), form_boundary=b)
    d = body["data"]
    assert status == 200 and d["status"] == "已接入" and d["functions"] >= 2, body
    print(f"2 上传代码包 ok: {d['functions']} 函数 / {d['call_edges']} 调用边 / wiki {d['wiki_pages']} 页 repo={d['id']}")
    ok += 1

    # 入口②：技术文档上传（双写 user_doc + kg 管线）
    rid = d["id"]
    tech = ("## 优惠券使用规则\n\n满 100 减 20，每单限用一张；退款时按比例回退优惠券；优惠券不可与积分同享。" * 3).encode("utf-8")
    b = uuid.uuid4().hex
    status, body = call("POST", "/api/knowledge/assets/upload", token, data=multipart({"kind": "tech_doc", "repo_id": str(rid), "title": "优惠券使用规则（冒烟）"}, ("file", "coupon-rules.md", tech), b), form_boundary=b)
    assert status == 200 and body["data"]["doc_key"].startswith("userdoc:"), body
    print(f"3 技术文档上传 ok: {body['data']['chars']} 字 doc_key={body['data']['doc_key'][:24]}… kg={body['data']['kg_doc_key'][:24]}…")

    # 入口②：历史缺陷 csv 导入
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["标题", "描述", "严重", "状态", "模块", "需求编号"])
    w.writerow(["优惠券叠加使用", "满减券与折扣券可叠加，资损", "P0", "已修复", "coupon", ""])
    w.writerow(["购物车总价取整错误", "四舍五入方向反了导致分账差一分钱", "P1", "新建", "cart", ""])
    b = uuid.uuid4().hex
    status, body = call("POST", "/api/knowledge/assets/upload", token, data=multipart({"kind": "defect", "repo_id": str(rid)}, ("file", "defects.csv", buf.getvalue().encode("utf-8-sig")), b), form_boundary=b)
    assert status == 200 and body["data"]["imported"] == 2, body
    codes = body["data"]["defect_codes"]
    print(f"4 历史缺陷导入 ok: {body['data']['imported']} 条 {codes}")

    # 闭环检索验证：新资产能被同一查询召回（模拟生成侧 bugs/知识召回）
    time.sleep(2)
    status, body = call("GET", "/api/knowledge/search?q=" + urllib.request.quote("优惠券 叠加 资损") + f"&repo_id={rid}", token)
    kinds = [h["kind"] for h in body["data"]]
    assert status == 200 and "defect" in kinds and "user_doc" in kinds, (kinds, body)
    print(f"5 闭环检索 ok: 命中类型 {kinds}（缺陷+知识文档同时召回）")

    status, body = call("GET", "/api/knowledge/assets/summary", token)
    print(f"6 闭环总览 ok: kinds={body['data']['kinds']} defects_open={body['data']['defects_open']}")

    # 清理（冒烟数据不入正式库）

    for c in codes:
        # 缺陷行删库走 SQL（无删除端点）——冒烟脚本直连测试库同级 dev 库不可取，改用检索删除端点清 rag 行
        call("POST", f"/api/knowledge/documents/delete?doc_key=defect:{c}", token)
    print(f"7 清理: 已移除缺陷检索行；冒烟仓库 upload://{SLUG}（repo={rid}）与缺陷记录请稍后随 reset 或保留作样例")
    print(f"SMOKE PASS ({ok + 4}/6 核心步骤)")


if __name__ == "__main__":
    main()
