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


def lan_ip() -> str:
    """优先取 192.168.* 的真实局域网网卡（跳过 WSL/虚拟网卡），用于提示手机访问地址。"""
    import socket

    try:
        infos = socket.gethostbyname_ex(socket.gethostname())[2]
        privates = [ip for ip in infos if not ip.startswith("127.")]
        preferred = [ip for ip in privates if ip.startswith("192.168.")]
        pool = preferred or privates
        if pool:
            return sorted(pool)[0]
    except OSError:
        pass
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.168.255.255", 1))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def _tcp_ok(host: str, port: int, timeout: float) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def main() -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    RUN.mkdir(parents=True, exist_ok=True)
    gateway_port = int(os.environ.get("GATEWAY_PORT", "8000"))

    print("[dev_up] 启动后端（单体：gateway + 9 服务进程内）…")
    if not port_open(gateway_port):
        # DB 兜底（WSL 中继故障自动直连）在 services.shared.config 统一处理
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
                    [pnpm, "--dir", "frontend", "dev", "--port", str(fe_port), "--strictPort", "--host", "0.0.0.0"],
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

    fe_port = os.environ.get("FRONTEND_PORT", "5173")
    print(f"[dev_up] 完成：backend=http://127.0.0.1:{gateway_port}  frontend=http://127.0.0.1:{fe_port}")
    print(f"[dev_up] 手机访问（同一 Wi-Fi）：http://{lan_ip()}:{fe_port}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
