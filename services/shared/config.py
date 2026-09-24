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

    # 沙箱：docker = 真实容器；fake = 假执行器
    sandbox_mode: str = "fake"

    log_level: str = "INFO"
    gateway_port: int = 8000
    frontend_port: int = 5173
    grpc_timeout_s: float = 10.0

    # 本地仓库检出根目录
    repo_root: str = "data/repos"

    # gRPC 目标主机：空 = 按服务名解析（docker 网络）；本地默认 127.0.0.1
    service_host: str = "127.0.0.1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
