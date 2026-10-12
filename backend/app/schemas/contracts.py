"""契约域 API 请求模型（注册 / 影响分析 / 定向重生成）。"""

from pydantic import BaseModel, Field


class ContractCreateIn(BaseModel):
    name: str = Field(min_length=1)
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
    # 契约影响面的定向重生成必须落在具体仓库（原路由层 400「repo_id 必填」）
    repo_id: int = Field(strict=True, ge=1, le=2147483647)
    target_function: str = Field(min_length=1)
    reason: str = "contract-impact"
    source_req: str = ""
