"""本地编排（Linux/macOS/无 WSL 环境）：单体后端 + 前端。"""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # backend/（Python 工程根）
FRONTEND_DIR = ROOT.parent / "frontend"  # 仓库根/frontend
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


PG_TUNNEL_PORT = 15432
PG_TUNNEL_URL = f"postgresql+psycopg://admin:testforge@127.0.0.1:{PG_TUNNEL_PORT}/testforge"


def _pg_handshake_ok(url: str, timeout: float = 4.0) -> bool:
    """真实 PG 握手探测（WSL 中继/网络的典型病：TCP 通但数据面死）。"""
    try:
        from sqlalchemy import create_engine, text

        eng = create_engine(url, connect_args={"connect_timeout": timeout, "pool_pre_ping": False})
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
        return True
    except Exception:  # noqa: BLE001
        return False


def ensure_pg_tunnel() -> bool:
    """确保 wsl 控制台管道隧道活着（PG 数据面最稳的通道）。健康返回 True。"""
    if _pg_handshake_ok(PG_TUNNEL_URL):
        return True
    tunnel = ROOT / "scripts" / "pg_tunnel.py"
    logf = open(LOGS / "pg_tunnel.log", "ab")
    subprocess.Popen(
        [sys.executable, "-u", str(tunnel)],
        cwd=ROOT,
        stdout=logf,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    for _ in range(10):
        time.sleep(1)
        if _pg_handshake_ok(PG_TUNNEL_URL):
            print(f"  ~ PG 隧道已建立：127.0.0.1:{PG_TUNNEL_PORT} -> wsl:5432")
            return True
    print("  ! PG 隧道未能建立（见 .run/logs/pg_tunnel.log）")
    return False


def resolve_db_url(env_url: str) -> str:
    """env/.env 的 DATABASE_URL 不健康时，依次尝试镜像模式 LAN IP 与 WSL NAT IP 直连。"""
    candidates: list[str] = [lan_ip()]
    try:
        out = subprocess.run(
            ["wsl", "-e", "bash", "-lc", "hostname -I"],
            capture_output=True, text=True, timeout=15, encoding="utf-8", errors="replace",
        ).stdout.split()
        candidates += [x for x in out if x.count(".") == 3]
    except (OSError, subprocess.SubprocessError):
        pass
    for ip in dict.fromkeys(candidates):  # 去重保序
        if ip == "127.0.0.1":
            continue
        url = f"postgresql+psycopg://admin:testforge@{ip}:5432/testforge"
        if _pg_handshake_ok(url):
            return url
    return ""


def _env_database_url() -> str:
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip()
    return os.environ.get("DATABASE_URL", "")


def main() -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    RUN.mkdir(parents=True, exist_ok=True)
    gateway_port = int(os.environ.get("GATEWAY_PORT", "8000"))

    print("[dev_up] 启动后端（单体：gateway + 9 服务进程内）…")
    if not port_open(gateway_port):
        env = {**os.environ, "PYTHONPATH": str(ROOT), "MONO_MODE": os.environ.get("MONO_MODE", "1")}
        env_url = os.environ.get("DATABASE_URL") or _env_database_url()
        if not _pg_handshake_ok(env_url):
            # .env 指向的 PG 不可达：优先 wsl 控制台隧道，其次各直连候选
            if ensure_pg_tunnel():
                env["DATABASE_URL"] = PG_TUNNEL_URL
                print(f"  ~ DATABASE_URL 走隧道：127.0.0.1:{PG_TUNNEL_PORT}")
            else:
                db_url = resolve_db_url(env_url)
                if db_url:
                    env["DATABASE_URL"] = db_url
                    print(f"  ~ .env DATABASE_URL 不健康，已切换直连：{db_url.split('@')[1]}")
        logf = open(LOGS / "gateway.log", "ab")
        proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", str(gateway_port)],
            cwd=ROOT,
            stdout=logf,
            stderr=subprocess.STDOUT,
            env={
                **os.environ,
                "PYTHONPATH": str(ROOT),
                "MONO_MODE": os.environ.get("MONO_MODE", "1"),
                # 本地开发/验收夹具（seed_real、demo-mX 用 file:// 本地仓库）需要显式开启；
                # 生产 compose 不设置该变量，仓库接入只收远程 Git URL
                "TF_ALLOW_LOCAL_REPO_URL": os.environ.get("TF_ALLOW_LOCAL_REPO_URL", "1"),
            },
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
                    [pnpm, "--dir", str(FRONTEND_DIR), "dev", "--port", str(fe_port), "--strictPort", "--host", "0.0.0.0"],
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
