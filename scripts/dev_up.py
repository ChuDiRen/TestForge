"""本地编排：一键拉起 7 个 gRPC 服务 + gateway + 前端（无 Docker 依赖）。

用法：
  python scripts/dev_up.py            # 后台拉起全部，健康检查通过后退出
  python scripts/dev_up.py --no-fe    # 只起后端
  python scripts/dev_down.py          # 全部停止
"""

import argparse
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

SERVICES = [
    ("trace-svc", [sys.executable, "-m", "services.trace_svc.main"], 50057),
    ("repo-svc", [sys.executable, "-m", "services.repo_svc.main"], 50051),
    ("wiki-builder", [sys.executable, "-m", "services.wiki_builder.main"], 50052),
    ("contract-registry", [sys.executable, "-m", "services.contract_registry.main"], 50053),
    ("req-svc", [sys.executable, "-m", "services.req_svc.main"], 50054),
    ("testgen-svc", [sys.executable, "-m", "services.testgen_svc.main"], 50055),
    ("runner-svc", [sys.executable, "-m", "services.runner_svc.main"], 50056),
]
GATEWAY_PORT = int(os.environ.get("GATEWAY_PORT", "8000"))
FRONTEND_PORT = int(os.environ.get("FRONTEND_PORT", "5173"))


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def spawn(name: str, cmd: list[str], port: int) -> None:
    if port_open(port):
        print(f"  = {name} 已在 :{port}，跳过")
        return
    logf = open(LOGS / f"{name}.log", "ab")
    proc = subprocess.Popen(
        cmd,
        cwd=ROOT,
        stdout=logf,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )
    (RUN / f"{name}.pid").write_text(str(proc.pid))
    print(f"  + {name} pid={proc.pid} -> :{port}")


def wait_port(port: int, timeout: float, label: str) -> bool:
    dl = time.time() + timeout
    while time.time() < dl:
        if port_open(port):
            return True
        time.sleep(0.4)
    print(f"  ! {label} :{port} 超时未就绪（看 .run/logs/）")
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fe", action="store_true", help="不启动前端")
    args = ap.parse_args()

    LOGS.mkdir(parents=True, exist_ok=True)
    RUN.mkdir(parents=True, exist_ok=True)

    print("[dev_up] 启动 gRPC 服务…")
    for name, cmd, port in SERVICES:
        spawn(name, cmd, port)
    for _, _, port in SERVICES:
        wait_port(port, 20, name)

    print("[dev_up] 启动 gateway…")
    spawn(
        "gateway",
        [sys.executable, "-m", "uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", str(GATEWAY_PORT), "--log-level", "warning"],
        GATEWAY_PORT,
    )
    wait_port(GATEWAY_PORT, 25, "gateway")

    if not args.no_fe:
        print("[dev_up] 启动前端 (vite)…")
        if not port_open(FRONTEND_PORT):
            pnpm = shutil.which("pnpm.cmd") or shutil.which("pnpm")
            if pnpm is None:
                print("  ! 未找到 pnpm，跳过前端（make install 先装依赖）")
            else:
                logf = open(LOGS / "frontend.log", "ab")
                proc = subprocess.Popen(
                    [pnpm, "--dir", "frontend", "dev", "--port", str(FRONTEND_PORT), "--strictPort"],
                    cwd=ROOT,
                    stdout=logf,
                    stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    shell=False,
                )
                (RUN / "frontend.pid").write_text(str(proc.pid))
                print(f"  + frontend pid={proc.pid} -> :{FRONTEND_PORT}")
        else:
            print(f"  = frontend 已在 :{FRONTEND_PORT}，跳过")
        wait_port(FRONTEND_PORT, 40, "frontend")

    print("[dev_up] 完成。gateway=http://127.0.0.1:%d  frontend=http://127.0.0.1:%d" % (GATEWAY_PORT, FRONTEND_PORT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
