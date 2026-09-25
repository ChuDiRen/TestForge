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

    # LLM：DeepSeek（OpenAI 兼容）。Key 必填——系统无任何确定性假实现
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"

    # 沙箱：local = 本机子进程真实 pytest（默认）；docker = 真实容器（--network none / 512m / 1cpu）
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

    # 任务队列：gateway 进程内 worker 并发数
    job_workers: int = 2

    # Webhook 共享密钥：空 = 局域网信任模式不校验；设置后要求请求头 X-Webhook-Secret 匹配
    webhook_secret: str = ""

    # 认证：token 签名密钥（泄露可伪造身份，生产必须改）；首启无用户时用 admin_password 引导管理员
    secret_key: str = "testforge-local-secret"
    admin_password: str = "testforge-admin"

    # 执行记录保留条数（超出自动清理最旧记录）
    runs_retention: int = 500


@lru_cache
def get_settings() -> Settings:
    return Settings()
