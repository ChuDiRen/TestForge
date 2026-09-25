"""本地编排（Linux/macOS/无 WSL 环境）：单体后端 + 前端。"""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / ".run"
LOGS = RUN / "logs"


def port_open(port: int) -> bool:
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
    gateway_port = int(os.environ.get("GATEWAY_PORT", "8000"))

    print("[dev_up] 启动后端（单体：gateway + 9 服务进程内）…")
    if not port_open(gateway_port):
        logf = open(LOGS / "gateway.log", "ab")
        proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", str(gateway_port)],
            cwd=ROOT,
            stdout=logf,
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONPATH": str(ROOT), "MONO_MODE": os.environ.get("MONO_MODE", "1")},
        )
        (RUN / "gateway.pid").write_text(str(proc.pid))
        print(f"  + backend pid={proc.pid} -> :{gateway_port}")
    else:
        print(f"  = backend 已在 :{gateway_port}，跳过")
    wait_port(gateway_port, 30, "backend")

    if "--no-fe" not in sys.argv:
        fe_port = int(os.environ.get("FRONTEND_PORT", "5173"))
        print("[dev_up] 启动前端 (vite)…")
        if not port_open(fe_port):
            pnpm = shutil.which("pnpm.cmd") or shutil.which("pnpm")
            if pnpm:
                logf = open(LOGS / "frontend.log", "ab")
                proc = subprocess.Popen(
                    [pnpm, "--dir", "frontend", "dev", "--port", str(fe_port), "--strictPort", "--host", "127.0.0.1"],
                    cwd=ROOT,
                    stdout=logf,
                    stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                (RUN / "frontend.pid").write_text(str(proc.pid))
                print(f"  + frontend pid={proc.pid} -> :{fe_port}")
        else:
            print(f"  = frontend 已在 :{fe_port}，跳过")
        wait_port(fe_port, 40, "frontend")

    print(f"[dev_up] 完成：backend=http://127.0.0.1:{gateway_port}  frontend=http://127.0.0.1:{os.environ.get('FRONTEND_PORT', '5173')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
