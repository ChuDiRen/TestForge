"""停止 dev_up 拉起的全部进程（按 .run/*.pid）。"""

import os
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / ".run"


def kill_pid(pid: int) -> None:
    try:
        if os.name == "nt":
            subprocess_terminate(pid)
        else:
            import signal as sig

            sig.send_signal = None  # noqa
            os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass


def subprocess_terminate(pid: int) -> None:
    import subprocess

    # taskkill /T 连子进程一起杀（uvicorn/vite 的 shell 子进程）
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)


def main() -> int:
    if not RUN.exists():
        print("无 .run 目录，nothing to do")
        return 0
    stopped = 0
    for pidf in sorted(RUN.glob("*.pid")):
        name = pidf.stem
        if name == "frontend":
            continue  # 前端在 Windows 侧单独处理
        try:
            pid = int(pidf.read_text().strip())
        except ValueError:
            continue
        kill_pid(pid)
        pidf.unlink(missing_ok=True)
        stopped += 1
        print(f"  - {name} (pid={pid}) killed")
    # Windows 侧前端
    fpidf = RUN / "frontend.pid"
    if fpidf.exists():
        try:
            kill_pid(int(fpidf.read_text().strip()))
        except (ValueError, ProcessLookupError):
            pass
        fpidf.unlink(missing_ok=True)
        stopped += 1
        print("  - frontend killed")
    print(f"[dev_down] stopped {stopped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
