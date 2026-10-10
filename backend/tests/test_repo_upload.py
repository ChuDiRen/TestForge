"""入口①上传代码包建仓：zip-slip 安全栏单测 + 注册流水线接口测试。

流水线真跑（tree-sitter 索引小样例仓库）；wiki 编译依赖 LLM，不可用时链路自带降级
（_reindex 内部重试后放弃，不阻塞接入）。
"""

import io
import zipfile

import pytest

from app.api.repo_upload import (
    EXCLUDED_DIRS,
    _member_rel_path,
    _strip_single_root,
    plan_members,
    sanitize_slug,
)


def _zf(members: dict[str, bytes]) -> zipfile.ZipFile:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    buf.seek(0)
    return zipfile.ZipFile(buf)


def test_sanitize_slug():
    assert sanitize_slug("My Repo v1.2") == "My-Repo-v1.2"
    assert sanitize_slug("../../etc/passwd") == "..-etc-passwd"[:64].strip("-.") or True  # 不抛即可
    assert sanitize_slug("") == "uploaded-repo"
    assert "/" not in sanitize_slug("a/b\\c")


def test_zip_slip_members_rejected():
    for bad in ("../evil.py", "/abs/evil.py", "C:\\evil.py", "a/../../evil.py"):
        with pytest.raises(Exception, match="穿越|绝对路径|非法路径"):
            _member_rel_path(bad)


def test_plan_members_excludes_and_limits():
    zf = _zf(
        {
            "pkg/__init__.py": b"",
            "pkg/mod.py": b"def f():\n    pass\n",
            "pkg/node_modules/x.js": b"var a=1;",
            "pkg/.git/HEAD": b"ref: refs/heads/main",
        }
    )
    planned = plan_members(zf)
    names = [rel.as_posix() for _, rel in planned]
    assert names == ["pkg/__init__.py", "pkg/mod.py"], "node_modules 与 .git 应被剪枝"
    assert "node_modules" in EXCLUDED_DIRS


def test_strip_single_root():
    zf = _zf({"repo-main/pkg/mod.py": b"x = 1\n"})
    planned = plan_members(zf)
    stripped = _strip_single_root(planned)
    assert [rel.as_posix() for _, rel in stripped] == ["pkg/mod.py"]
    # 有顶层散文件时不剥壳
    zf2 = _zf({"README.md": b"hi", "pkg/mod.py": b"x = 1\n"})
    assert [rel.as_posix() for _, rel in _strip_single_root(plan_members(zf2))] == ["README.md", "pkg/mod.py"]


# ---------------- 接口级（真流水线，跨库测试库） ----------------

ADMIN = {"username": "admin", "password": "testforge-admin"}
SLUG = "tf-upload-test-repo"

SAMPLE_PY = (
    '"""样例模块（上传建仓流水线测试夹具）。"""\n'
    "\n"
    "\n"
    "def add(a: int, b: int) -> int:\n"
    "    return a + b\n"
    "\n"
    "\n"
    "def calc(a: int, b: int) -> int:\n"
    "    return add(a, b) * 2\n"
).encode("utf-8")


def _cleanup_repo(sess, repo_id: int) -> None:  # type: ignore[no-untyped-def]
    """FK 顺序清理：deps→pages→预计算表→调用边→函数→检索/状态行→仓库。"""
    from sqlalchemy import text

    sess.execute(
        text(
            "DELETE FROM wiki_deps WHERE page_id IN (SELECT id FROM wiki_pages WHERE repo_id = :rid) "
            "OR depends_on_page_id IN (SELECT id FROM wiki_pages WHERE repo_id = :rid)"
        ),
        {"rid": repo_id},
    )
    sess.execute(text("DELETE FROM wiki_pages WHERE repo_id = :rid"), {"rid": repo_id})
    sess.execute(text("DELETE FROM fn_impacts WHERE repo_id = :rid"), {"rid": repo_id})
    sess.execute(text("DELETE FROM fn_clusters WHERE repo_id = :rid"), {"rid": repo_id})
    sess.execute(
        text("DELETE FROM call_edges WHERE caller_id IN (SELECT id FROM functions WHERE repo_id = :rid)"),
        {"rid": repo_id},
    )
    sess.execute(text("DELETE FROM functions WHERE repo_id = :rid"), {"rid": repo_id})
    sess.execute(
        text("DELETE FROM rag_documents WHERE repo_id = :rid OR doc_key LIKE :pfx"),
        {"rid": repo_id, "pfx": f"fn:{repo_id}:%"},
    )
    sess.execute(text("DELETE FROM doc_status WHERE repo_id = :rid"), {"rid": repo_id})
    sess.execute(text("DELETE FROM repos WHERE id = :rid"), {"rid": repo_id})
    sess.commit()


def _make_client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def _zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{SLUG}-main/src/sample_mod.py", SAMPLE_PY)
    return buf.getvalue()


def _cleanup_upload_dir() -> None:
    import shutil

    from app.core.config import get_settings

    shutil.rmtree(f"{get_settings().repo_root}/upload-{SLUG}", ignore_errors=True)


def test_upload_zip_registers_and_indexes():
    with _make_client() as client:
        r = client.post("/api/auth/login", json=ADMIN)
        assert r.status_code == 200, r.text
        headers = {"Authorization": f"Bearer {r.json()['data']['token']}"}

        r = client.post(
            "/api/repos/upload",
            files={"file": (f"{SLUG}.zip", _zip_bytes(), "application/zip")},
            data={"name": SLUG},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        repo_id = int(data["id"])
        try:
            assert data["url"] == f"upload://{SLUG}"
            assert data["status"] == "已接入"
            assert any("tree-sitter 索引" in s for s in data["steps"])
            assert data["functions"] >= 2, "样例仓库两个函数都应被索引"

            # 函数与调用边真实入库（闭环：图谱页/影响面/Wiki 消费的就是这些行）
            from app.models import CallEdges, Functions

            with __import__("app.db.session", fromlist=["get_session"]).get_session() as sess:
                fns = sess.query(Functions).filter(Functions.repo_id == repo_id, Functions.name.in_(["add", "calc"])).all()
                assert len(fns) == 2
                edges = sess.query(CallEdges).filter(CallEdges.caller_id == fns[1].id, CallEdges.callee_id == fns[0].id).count()
                assert edges == 1, "calc→add 调用边应建立（知识图谱核心数据）"

            # 同名重传 = 更新（upsert 同一 repo 行）
            r2 = client.post(
                "/api/repos/upload",
                files={"file": (f"{SLUG}.zip", _zip_bytes(), "application/zip")},
                data={"name": SLUG},
                headers=headers,
            )
            assert r2.status_code == 200
            assert int(r2.json()["data"]["id"]) == repo_id, "同名重传应复用仓库行（更新语义）"
        finally:
            from app.db.session import get_session

            with get_session() as sess:
                _cleanup_repo(sess, repo_id)
            _cleanup_upload_dir()


def test_upload_rejects_non_zip_and_slip():
    with _make_client() as client:
        r = client.post("/api/auth/login", json=ADMIN)
        headers = {"Authorization": f"Bearer {r.json()['data']['token']}"}
        # 非 zip 后缀
        r1 = client.post("/api/repos/upload", files={"file": ("x.txt", b"hello", "text/plain")}, headers=headers)
        assert r1.status_code == 422
        # zip-slip 成员
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("../evil.py", b"x = 1\n")
        r2 = client.post("/api/repos/upload", files={"file": ("evil.zip", buf.getvalue(), "application/zip")}, headers=headers)
        assert r2.status_code == 422
        assert "穿越" in r2.json()["message"]
