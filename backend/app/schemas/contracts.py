"""契约域 API 请求模型（注册 / 影响分析 / 定向重生成）。"""

from pydantic import BaseModel


class ContractCreateIn(BaseModel):
    name: str = ""
    type: str = "rest"  # rest | grpc | ...
    provider_repo: str = ""
    version: str = "v1.0.0"
    spec: dict = {}
    consumers: list[str] = []


class ContractImpactIn(BaseModel):
    to_v: str = ""


class ContractRegenerateIn(BaseModel):
    to_v: str = ""


class RegenerateAffectedIn(BaseModel):
    repo_id: int = 0
    target_function: str = ""
    reason: str = "contract-impact"
    source_req: str = ""
