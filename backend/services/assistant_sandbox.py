"""AI 助手执行沙箱（对标 deepagents sandboxes 的 thread-scoped 设计）。

实现 deepagents `BaseSandbox` 最小面（id / execute / upload_files / download_files），
文件操作由框架基类在 execute 之上构建。隔离策略：

- 工作区 per 会话：data/assistant_sandbox/{thread_id}/，互不可见
- 环境白名单：只传系统必需变量，宿主机 .env（DB/LLM 密钥）不进沙箱进程
- 超时 + 输出截断；命令经 shell 执行，退出码/输出回填给模型
- 仓库源码经 CompositeBackend 以 /repo/ 前缀只读挂载，写仓库必须走正式回写管线
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys
import time
from pathlib import Path

from deepagents.backends import CompositeBackend, FilesystemBackend
from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    SandboxBackendProtocol,
)
from deepagents.backends.sandbox import BaseSandbox

MAX_OUTPUT_CHARS = 20_000
DEFAULT_TIMEOUT_S = 60
MAX_TIMEOUT_S = 180

# Windows 进程必需的最小环境；.env 注入的 DATABASE_URL / LLM_API_KEY 等一律不透传
_ENV_ALLOWLIST = ("SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "TEMP", "TMP", "WINDIR", "USERNAME")


def _sandbox_env(workspace: Path) -> dict:
    """构造沙箱进程环境：系统白名单 + 解释器目录 PATH，宿主机 .env 密钥不透传。"""
    env = {k: v for k, v in os.environ.items() if k.upper() in _ENV_ALLOWLIST}
    exe_dir = os.path.dirname(sys.executable)
    system32 = os.path.join(env.get("SYSTEMROOT", r"C:\Windows"), "System32")
    env["PATH"] = os.pathsep.join([exe_dir, system32, env.get("COMSPEC", r"C:\Windows\System32\cmd.exe")])
    env["PYTHONPATH"] = str(workspace)
    env["PYTHONIOENCODING"] = "utf-8"
    env["HOME"] = str(workspace)
    return env


def sandbox_workspace(thread_id: int) -> Path:
    ws = Path("data/assistant_sandbox") / f"thread-{thread_id}"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


class TestForgeSandbox(BaseSandbox):
    """thread-scoped 本机执行沙箱：子进程 + 环境白名单 + 超时 + 输出截断。"""

    def __init__(self, thread_id: int) -> None:
        self._thread_id = thread_id
        self._ws = sandbox_workspace(thread_id)

    @property
    def id(self) -> str:
        return f"testforge-local-thread-{self._thread_id}"

    @property
    def workspace(self) -> Path:
        return self._ws

    def _safe_path(self, rel: str) -> Path:
        p = (self._ws / rel.lstrip("/")).resolve()
        if not str(p).startswith(str(self._ws.resolve())):
            raise ValueError(f"路径越界: {rel}")
        return p

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        t0 = time.time()
        eff_timeout = min(max(timeout or DEFAULT_TIMEOUT_S, 1), MAX_TIMEOUT_S)
        env = _sandbox_env(self._ws)
        try:
            proc = subprocess.run(  # noqa: S602 —— 沙箱语义即执行任意模型给的命令，靠隔离而非解析命令
                command,
                shell=True,
                cwd=self._ws,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=eff_timeout,
                env=env,
            )
            output = (proc.stdout or "") + (proc.stderr or "")
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            output = f"执行超时（>{eff_timeout}s），已终止"
            exit_code = -1
        except Exception as exc:  # noqa: BLE001
            output = f"执行失败: {exc}"
            exit_code = -1
        truncated = len(output) > MAX_OUTPUT_CHARS
        if truncated:
            output = output[:MAX_OUTPUT_CHARS] + f"\n…（输出已截断，共 {len(output)} 字符）"
        if not output.strip():
            output = "（无输出）"
        output = f"[exit {exit_code} · {time.time() - t0:.1f}s]\n{output}"
        return ExecuteResponse(output=output, exit_code=exit_code, truncated=truncated)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        responses: list[FileUploadResponse] = []
        for path, content in files:
            try:
                target = self._safe_path(path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                responses.append(FileUploadResponse(path=path))
            except Exception as exc:  # noqa: BLE001
                responses.append(FileUploadResponse(path=path, error=str(exc)))
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses: list[FileDownloadResponse] = []
        for path in paths:
            try:
                target = self._safe_path(path)
                if not target.is_file():
                    responses.append(FileDownloadResponse(path=path, error="file_not_found"))
                    continue
                content = base64.b64encode(target.read_bytes())
                responses.append(FileDownloadResponse(path=path, content=content))
            except Exception as exc:  # noqa: BLE001
                responses.append(FileDownloadResponse(path=path, error=str(exc)))
        return responses


def memory_backend():
    """跨会话记忆目录（可写）：模型经 /memory/ 前缀 edit_file 更新，MemoryMiddleware 读取注入。"""
    root = Path("data/assistant_memory")
    root.mkdir(parents=True, exist_ok=True)
    mem_file = root / "memory.md"
    if not mem_file.exists():
        mem_file.write_text(
            "# TestForge 智能体记忆\n\n"
            "<!-- 跨会话持久：项目约定、用户偏好、常见误区。模型可编辑本文件。 -->\n\n"
            "## 项目约定\n- 测试框架：pytest\n- 用例分层：ut / fn / api / e2e / contract\n",
            encoding="utf-8",
        )
    return FilesystemBackend(root_dir=str(root), virtual_mode=True)


def routed_backend(thread_id: int, repo_root: str | None):
    """沙箱为默认 backend（execute + 工作区文件）；/repo/ 只读挂仓库检出；/memory/ 可写跨会话记忆。"""
    sandbox = TestForgeSandbox(thread_id)

    class _Routed(CompositeBackend, SandboxBackendProtocol):
        """文件操作按前缀路由；execute/透传给沙箱，使框架授予 execute 工具。"""

        @property
        def id(self) -> str:  # noqa: D102
            return sandbox.id

        def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:  # noqa: D102
            return sandbox.execute(command, timeout=timeout)

        async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:  # noqa: D102
            return sandbox.execute(command, timeout=timeout)

    routes: dict = {"/memory/": memory_backend()}
    if repo_root:
        routes["/repo/"] = FilesystemBackend(root_dir=repo_root, virtual_mode=True)
    return _Routed(default=sandbox, routes=routes)
