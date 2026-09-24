"""Windows 侧编排：WSL 拉起后端 + Windows 拉起 vite 前端，等全绿后退出。"""

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / ".run"
LOGS = RUN / "logs"


def port_open(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_port(port: int, timeout: float, label: str) -> bool:
    dl = time.time() + timeout
    while time.time() < dl:
        if port_open(port):
            return True
        time.sleep(0.4)
    print(f"  ! {label} :{port} 超时未就绪")
    return False


def main() -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    RUN.mkdir(parents=True, exist_ok=True)

    print("[dev_up] WSL 后端（7 gRPC + gateway）…")
    r = subprocess.run(
        ["wsl.exe", "-e", "bash", "-lc", "cd /mnt/e/TestForge && bash scripts/dev_up_wsl.sh"],
        cwd=ROOT,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "WSLENV": ""},
    )
    if r.returncode != 0:
        print("[dev_up] WSL 后端失败")
        return 1

    print("[dev_up] Windows 前端 (vite)…")
    FRONTEND_PORT = int(os.environ.get("FRONTEND_PORT", "5173"))
    if not port_open(FRONTEND_PORT):
        import shutil

        pnpm = shutil.which("pnpm.cmd") or shutil.which("pnpm")
        if pnpm:
            logf = open(LOGS / "frontend.log", "ab")
            proc = subprocess.Popen(
                [pnpm, "--dir", "frontend", "dev", "--port", str(FRONTEND_PORT), "--strictPort", "--host", "127.0.0.1"],
                cwd=ROOT,
                stdout=logf,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            (RUN / "frontend.pid").write_text(str(proc.pid))
            print(f"  + frontend pid={proc.pid} -> :{FRONTEND_PORT}")
        else:
            print("  ! 未找到 pnpm，跳过前端")
    else:
        print(f"  = frontend 已在 :{FRONTEND_PORT}，跳过")
    wait_port(FRONTEND_PORT, 40, "frontend")

    print("[dev_up] 完成：gateway=http://127.0.0.1:8000  frontend=http://127.0.0.1:5173")
    return 0


if __name__ == "__main__":
    sys.exit(main())
