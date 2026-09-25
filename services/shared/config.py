"""全局配置（pydantic-settings，读根目录 .env）。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

VERSION = "0.1.0"

# 服务名 → gRPC 端口（唯一事实源，compose/Makefile 与此处保持一致）
GRPC_PORTS: dict[str, int] = {
    "repo-svc": 50051,
    "wiki-builder": 50052,
    "contract-registry": 50053,
    "req-svc": 50054,
    "testgen-svc": 50055,
    "runner-svc": 50056,
    "trace-svc": 50057,
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://admin:testforge@127.0.0.1:5432/testforge"
    redis_url: str = "redis://127.0.0.1:6379/0"

    # LLM：real = OpenAI 兼容 API；mock = 固定样例（无 Key 全流程可跑）
    llm_mode: str = "mock"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"

    # 沙箱：docker = 真实容器；local = 本机子进程真实 pytest（默认，数据全真实）；fake = 确定性模拟（仅演示）
    sandbox_mode: str = "local"

    log_level: str = "INFO"
    gateway_port: int = 8000
    frontend_port: int = 5173
    grpc_timeout_s: float = 10.0

    # 本地仓库检出根目录
    repo_root: str = "data/repos"

    # 单体模式：全部服务并入一个进程（gRPC 调用改为进程内直调，仅暴露网关端口）
    mono: bool = True

    # gRPC 目标主机：空 = 按服务名解析（docker 网络）；本地默认 127.0.0.1
    service_host: str = "127.0.0.1"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.database_url = _stable_db_url(s.database_url)
    return s


def _stable_db_url(url: str) -> str:
    """Windows + WSL postgres 拓扑：localhost 中继不稳定时自动改写 host 为可达的 WSL IP。

    每进程只探测一次；非 127.0.0.1/localhost（docker 网络/远程）或本机可达时原样返回。
    """
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if parts.scheme.split("+")[0] != "postgresql":
        return url
    if parts.hostname not in ("127.0.0.1", "localhost"):
        return url
    import socket

    port = parts.port or 5432
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.8)
        if s.connect_ex(("127.0.0.1", port)) == 0:
            return url
    ip = _wsl_db_ip(port)
    if not ip:
        return url
    print(f"[config] localhost:{port} 不可达，DATABASE_URL 改走 WSL 直连 {ip}:{port}")
    return url.replace(parts.netloc, parts.netloc.replace(parts.hostname, ip, 1), 1)


def _wsl_db_ip(port: int) -> str:
    import subprocess

    try:
        out = subprocess.run(
            ["wsl", "-e", "bash", "-lc", "hostname -I"],
            capture_output=True, text=True, timeout=15, encoding="utf-8", errors="replace",
        ).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return ""
    import socket

    for ip in (x for x in out if x.count(".") == 3):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(2.0)
            if s.connect_ex((ip, port)) == 0:
                return ip
    return ""
