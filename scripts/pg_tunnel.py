"""Windows 侧 PG 隧道：127.0.0.1:15432 → wsl.exe 控制台管道 → WSL 内 127.0.0.1:5432。

为什么存在：WSL2 的 NAT 中继 / portproxy / 镜像端口同步在本机都会间歇性断流
（TCP 能通、数据面死），而 wsl.exe 控制台通道全天稳定。每个客户端连接对应
一条 `wsl -e bash` 管道进程，与 SQLAlchemy 连接池（默认 5+10）规模匹配。
"""

from __future__ import annotations

import os
import socketserver
import subprocess
import threading

LISTEN_PORT = int(os.environ.get("PG_TUNNEL_PORT", "15432"))
WSL_CMD = [
    "wsl.exe",
    "-e",
    "bash",
    "-c",
    "exec 3<>/dev/tcp/127.0.0.1/5432; cat >&3 <&0 & cat <&3",
]


def _pump(src, dst) -> None:  # type: ignore[no-untyped-def]
    try:
        while True:
            data = src.read(65536) if hasattr(src, "read") else src.recv(65536)
            if not data:
                break
            if hasattr(dst, "write"):
                dst.write(data)
                dst.flush()
            else:
                dst.sendall(data)
    except OSError:
        pass
    finally:
        try:
            src.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            dst.close()
        except Exception:  # noqa: BLE001
            pass


class _Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        proc = subprocess.Popen(
            WSL_CMD,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        up = threading.Thread(target=_pump, args=(self.request, proc.stdin), daemon=True)
        up.start()
        _pump(proc.stdout, self.request)  # 阻塞至 PG 侧或客户端关闭
        up.join(timeout=3)
        try:
            proc.kill()
        except OSError:
            pass


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> None:
    print(f"[pg-tunnel] 127.0.0.1:{LISTEN_PORT} -> wsl 127.0.0.1:5432", flush=True)
    with _Server(("127.0.0.1", LISTEN_PORT), _Handler) as srv:
        srv.serve_forever()


if __name__ == "__main__":
    main()
