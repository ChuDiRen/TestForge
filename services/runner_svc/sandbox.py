"""沙箱执行：docker（--network none / 512m / 1cpu）/ local（子进程）/ fake（确定性模拟）。

产物统一解析：junitxml（用例结果）+ coverage json（分支覆盖率）。
"""

from __future__ import annotations

import ast
import json
import logging
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from services.shared.config import get_settings

log = logging.getLogger("runner.sandbox")

SANDBOX_IMAGE = "testforge-sandbox:py312"


@dataclass
class SandboxResult:
    mode: str
    pass_total: int
    pass_count: int
    coverage: float
    status: str  # success | failed | error
    cases: dict[str, str] = field(default_factory=dict)  # TC-xxx -> passed|failed
    failures: list[dict] = field(default_factory=list)
    log: str = ""


def prepare_workspace(run_code: str, repo_checkout: Path) -> Path:
    """复制被测仓库（去 .git）到 data/runs/<run>/ws。"""
    ws = Path("data/runs") / run_code / "ws"
    if ws.exists():
        shutil.rmtree(ws, ignore_errors=True)
    ws.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        repo_checkout,
        ws,
        ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", "*.pyc", ".venv"),
    )
    return ws


def write_test_file(ws: Path, filename: str, source: str) -> Path:
    tests = ws / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "__init__.py").touch(exist_ok=True)
    tf = tests / filename
    tf.write_text(source, encoding="utf-8")
    # conftest：确保仓库根在 sys.path
    conftest = ws / "conftest.py"
    if not conftest.exists():
        conftest.write_text(
            "import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).parent))\n",
            encoding="utf-8",
        )
    return tf


def execute(run_code: str, ws: Path, test_files: list[str]) -> SandboxResult:
    mode = get_settings().sandbox_mode
    if mode == "docker":
        return _exec_docker(run_code, ws, test_files)
    if mode == "local":
        return _exec_local(run_code, ws, test_files)
    return _exec_fake(run_code, ws, test_files)


# ---------------- fake：确定性模拟（无 docker 环境全流程演示） ----------------


def _exec_fake(run_code: str, ws: Path, test_files: list[str]) -> SandboxResult:
    total = 0
    cases: dict[str, str] = {}
    for tf in test_files:
        path = ws / "tests" / tf
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            return SandboxResult(mode="fake", pass_total=0, pass_count=0, coverage=0.0, status="error", log=f"语法错误: {path.name}")
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                total += 1
                tc = _tc_of(node.name)
                cases[tc] = "passed"
    coverage = min(95.0, 55.0 + total * 3.0)  # 确定性估算
    log.info("fake sandbox: %d cases simulated pass, coverage %.1f", total, coverage)
    return SandboxResult(
        mode="fake",
        pass_total=total,
        pass_count=total,
        coverage=round(coverage, 1),
        status="success",
        cases=cases,
        log=f"[fake] 模拟执行 {total} 用例全部通过（SANDBOX_MODE=fake）",
    )


def _tc_of(test_name: str) -> str:
    for part in test_name.split("_"):
        if part.upper().startswith("TC-") or (part.upper().startswith("TC") and part[2:].isdigit()):
            return part.upper().replace("TC", "TC-")
    return test_name


# ---------------- local：本机子进程真实执行（验证生成代码可跑） ----------------


def _exec_local(run_code: str, ws: Path, test_files: list[str]) -> SandboxResult:
    args = [sys.executable, "-m", "pytest", "-q", "--disable-warnings", "--junitxml=report.xml", f"--cov={_top_pkg(ws)}", "--cov-branch", "--cov-report=json:coverage.json", *test_files]
    return _run_pytest(run_code, ws, args, mode="local")


def _run_pytest(run_code: str, ws: Path, args: list[str], mode: str) -> SandboxResult:
    import os

    try:
        proc = subprocess.run(
            args,
            cwd=ws,
            capture_output=True,
            text=True,
            timeout=180,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONPATH": str(ws), "PYTHONIOENCODING": "utf-8"},
        )
        tail = (proc.stdout or "")[-2000:]
    except subprocess.TimeoutExpired:
        return SandboxResult(mode=mode, pass_total=0, pass_count=0, coverage=0.0, status="error", log="执行超时(180s)")
    report = ws / "report.xml"
    if not report.exists():
        return SandboxResult(mode=mode, pass_total=0, pass_count=0, coverage=0.0, status="error", log=f"pytest 未产出报告:\n{tail}")
    res = _parse_junit(report, mode)
    res.log = tail
    cov_file = ws / "coverage.json"
    if cov_file.exists():
        try:
            data = json.loads(cov_file.read_text(encoding="utf-8"))
            res.coverage = round(float(data["totals"]["percent_covered"]), 1)
        except Exception:  # noqa: BLE001
            pass
    return res


def _parse_junit(report: Path, mode: str) -> SandboxResult:
    root = ET.parse(report).getroot()
    suite = root.find("testsuite") if root.tag == "testsuites" else root
    assert suite is not None
    total = int(suite.get("tests", "0"))
    failures = int(suite.get("failures", "0")) + int(suite.get("errors", "0"))
    cases: dict[str, str] = {}
    fail_details: list[dict] = []
    for tc in suite.iter("testcase"):
        name = tc.get("name", "")
        ok = tc.find("failure") is None and tc.find("error") is None
        cases[_tc_of(name)] = "passed" if ok else "failed"
        if not ok:
            node = tc.find("failure") if tc.find("failure") is not None else tc.find("error")
            fail_details.append({"case": _tc_of(name), "test": name, "message": (node.text or "")[:800] if node is not None else ""})
    return SandboxResult(
        mode=mode,
        pass_total=total,
        pass_count=total - failures,
        coverage=0.0,
        status="success" if failures == 0 else "failed",
        cases=cases,
        failures=fail_details,
    )


def _top_pkg(ws: Path) -> str:
    for child in sorted(ws.iterdir()):
        if child.is_dir() and (child / "__init__.py").exists():
            return child.name
    return "."


# ---------------- docker：真实隔离沙箱 ----------------


def _exec_docker(run_code: str, ws: Path, test_files: list[str]) -> SandboxResult:
    import docker as docker_py

    client = docker_py.from_env()
    _ensure_image(client)
    args = ["python", "-m", "pytest", "-q", "--disable-warnings", "--junitxml=/ws/report.xml", f"--cov={_top_pkg(ws)}", "--cov-branch", "--cov-report=json:/ws/coverage.json", *[f"/ws/tests/{t}" for t in test_files]]
    container = client.containers.run(
        SANDBOX_IMAGE,
        command=args,
        mounts=[docker_py.types.Mount("/ws", str(ws.resolve()), type="bind")],
        network_mode="none",
        mem_limit="512m",
        nano_cpus=1_000_000_000,
        detach=True,
        working_dir="/ws",
        environment={"PYTHONPATH": "/ws", "PYTHONIOENCODING": "utf-8"},
    )
    try:
        out = container.wait(timeout=300)
        logs = container.logs().decode("utf-8", errors="replace")[-2000:]
        code = out.get("StatusCode", 1)
    finally:
        container.remove(force=True)
    report = ws / "report.xml"
    if not report.exists():
        return SandboxResult(mode="docker", pass_total=0, pass_count=0, coverage=0.0, status="error", log=f"容器退出码 {code}\n{logs}")
    res = _parse_junit(report, "docker")
    res.log = logs
    cov_file = ws / "coverage.json"
    if cov_file.exists():
        try:
            res.coverage = round(float(json.loads(cov_file.read_text(encoding="utf-8"))["totals"]["percent_covered"]), 1)
        except Exception:  # noqa: BLE001
            pass
    return res


def _ensure_image(client) -> None:  # type: ignore[no-untyped-def]
    try:
        client.images.get(SANDBOX_IMAGE)
        return
    except Exception:  # noqa: BLE001
        pass
    dockerfile = Path("deploy") / "sandbox.Dockerfile"
    if dockerfile.exists():
        log.info("building sandbox image %s …", SANDBOX_IMAGE)
        client.images.build(path=".", dockerfile=str(dockerfile), tag=SANDBOX_IMAGE, rm=True)
    else:
        client.images.pull("python:3.12-slim")
        log.warning("使用裸 python:3.12-slim（无 pytest，可能失败）；建议提供 deploy/sandbox.Dockerfile")
