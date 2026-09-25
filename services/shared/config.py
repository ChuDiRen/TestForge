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
    """Windows + WSL postgres 拓扑：localhost 中继数据面不稳（TCP 通但握手死）时，
    用真实 PG 握手探测，失败则改写 host 为可达的 WSL IP。每进程只探测一次。"""
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if parts.scheme.split("+")[0] != "postgresql":
        return url
    if parts.hostname not in ("127.0.0.1", "localhost"):
        return url
    if _pg_alive(url, 3.0):
        return url
    ip = _wsl_db_ip(parts.port or 5432)
    if not ip:
        return url
    new_url = url.replace(parts.netloc, parts.netloc.replace(parts.hostname, ip, 1), 1)
    if _pg_alive(new_url, 3.0):
        print(f"[config] localhost 中继数据面不通，DATABASE_URL 改走 WSL 直连 {ip}:{parts.port or 5432}")
        return new_url
    return url


def _pg_alive(url: str, timeout: float) -> bool:
    """真实 PG 握手探测（裸 TCP 通不代表数据面通——WSL 中继的典型症状）。"""
    from sqlalchemy import create_engine, text

    try:
        eng = create_engine(url, connect_args={"connect_timeout": timeout, "pool_pre_ping": False})
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
        return True
    except Exception:  # noqa: BLE001
        return False


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
