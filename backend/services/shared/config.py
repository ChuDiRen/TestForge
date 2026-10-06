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
    # LLM 角色路由（LightRAG 式四角色裁剪为三角色）：空 = 全部回退 llm_model。
    # extract=抽取/摘要（快、不开思考）；query=最终生成/报告（可用 deepseek-reasoner）；keyword=检索词提取（轻量）
    llm_model_extract: str = ""
    llm_model_query: str = ""
    llm_model_keyword: str = ""

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

    # 部署环境：dev（默认，宽松）/ prod（启动时强制安全检查：默认密钥直接拒启）
    env: str = "dev"

    def validate_for_prod(self) -> list[str]:
        """生产部署安全检查：返回违规项列表（空 = 通过）。gateway 启动时 env=prod 调用。"""
        problems: list[str] = []
        if self.secret_key == "testforge-local-secret":
            problems.append("SECRET_KEY 仍为默认值——token 可被伪造，必须设置强随机密钥")
        if self.admin_password == "testforge-admin":
            problems.append("ADMIN_PASSWORD 仍为默认值——必须设置强管理员口令")
        if not self.llm_api_key:
            problems.append("LLM_API_KEY 未配置——生成管线不可用；公司内网请指向内部 LLM 网关（llm_base_url）")
        if self.sandbox_mode != "docker":
            problems.append("sandbox_mode != docker——AI 执行沙箱未容器化隔离，生产建议 docker 模式")
        if self.allow_local_repo_url:
            problems.append("ALLOW_LOCAL_REPO_URL=true——生产环境禁止本地路径接入仓库")
        return problems

    # 执行记录保留条数（超出自动清理最旧记录）
    runs_retention: int = 500

    # 仓库接入是否允许本地路径（file:///盘符/绝对路径）。生产必须 False；
    # 仅本地开发/验收夹具经 TF_ALLOW_LOCAL_REPO_URL=1 显式开启
    allow_local_repo_url: bool = False

    # 上下文包 token 预算（生成管线六路上下文超限时按优先级从低到高裁剪）
    ctx_token_budget: int = 16000

    # 变更影响分析：反向传播深度上限（置信度随深度衰减 1/depth）
    impact_max_depth: int = 4


@lru_cache
def get_settings() -> Settings:
    return Settings()
