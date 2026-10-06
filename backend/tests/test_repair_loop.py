"""修复循环真回填单测：重生成代码必须写入沙箱工作区（而非原样重试）。"""

from pathlib import Path

from services.runner_svc import sandbox, service


class _FakeRes:
    def __init__(self, status: str) -> None:
        self.mode = "local"
        self.pass_total = 2
        self.pass_count = 2 if status == "success" else 0
        self.coverage = 42.0
        self.status = status
        self.cases: dict = {}
        self.failures: list = [] if status == "success" else [{"case": "TC-001", "test": "test_tc001", "message": "boom"}]
        self.log = ""


def test_repair_loop_writes_regen_source(tmp_path: Path, monkeypatch):
    """第 1 轮失败 → RegenerateAffected 返回 code_file → 必须覆盖工作区文件后重执行。"""
    calls = {"exec": 0, "regen": 0}
    written: list[str] = []

    def fake_prepare(run_code: str, repo_checkout: Path) -> Path:
        ws = tmp_path / "ws"
        ws.mkdir(parents=True, exist_ok=True)
        return ws

    def fake_write(ws: Path, filename: str, source: str) -> Path:
        written.append(source)
        target = ws / "tests" / filename
        target.parent.mkdir(exist_ok=True)
        target.write_text(source, encoding="utf-8")
        return target

    def fake_execute(run_code: str, ws: Path, test_files: list, only=None, cov_pkg: str = ""):
        calls["exec"] += 1
        return _FakeRes("failed" if calls["exec"] == 1 else "success")

    def fake_grpc_call(*args, **kwargs):
        calls["regen"] += 1
        return {"code_file": "NEW-GENERATED-SRC", "cases_total": 2}

    monkeypatch.setattr(sandbox, "prepare_workspace", fake_prepare)
    monkeypatch.setattr(sandbox, "write_test_file", fake_write)
    monkeypatch.setattr(sandbox, "execute", fake_execute)
    monkeypatch.setattr(service, "grpc_call", fake_grpc_call)

    cases = [{"code": "TC-001", "title": "t", "target_function": "sanitize_text", "module": "services.shared.sanitize"}]
    res = service.execute_suite("RUN-REPAIR-TEST", cases, "OLD-SRC", repo_id=0, trace_id="tr_x", req_code="")

    assert calls["regen"] == 1
    assert res["status"] == "success" and res["repair_rounds"] == 1
    # 首轮写入 OLD-SRC，修复轮必须以重生成代码覆盖
    assert written == ["OLD-SRC", "NEW-GENERATED-SRC"]
    assert res["timeline"][1]["regenerated"] is True


def test_repair_loop_survives_regen_failure(tmp_path: Path, monkeypatch):
    """RegenerateAffected 不可用时不得让执行链路崩掉——退化为原样重试。"""
    calls = {"exec": 0}

    def fake_prepare(run_code: str, repo_checkout: Path) -> Path:
        return tmp_path / "ws2"

    def fake_write(ws: Path, filename: str, source: str) -> Path:
        return ws / filename

    def fake_execute(run_code: str, ws: Path, test_files: list, only=None, cov_pkg: str = ""):
        calls["exec"] += 1
        return _FakeRes("failed" if calls["exec"] <= 2 else "success")

    def boom(*args, **kwargs):
        raise RuntimeError("regen svc down")

    monkeypatch.setattr(sandbox, "prepare_workspace", fake_prepare)
    monkeypatch.setattr(sandbox, "write_test_file", fake_write)
    monkeypatch.setattr(sandbox, "execute", fake_execute)
    monkeypatch.setattr(service, "grpc_call", boom)

    cases = [{"code": "TC-001", "title": "t", "target_function": "f", "module": "m"}]
    res = service.execute_suite("RUN-REPAIR-FALLBACK", cases, "SRC", repo_id=0, trace_id="", req_code="")
    assert res["status"] == "success" and res["repair_rounds"] == 2
    assert res["timeline"][1]["regenerated"] is False
