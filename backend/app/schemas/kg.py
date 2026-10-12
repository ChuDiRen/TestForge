"""知识图谱域（KG/LightRAG）API 请求模型。

/workspace+repo_id 由各路由的 _resolve_ws 统一解析。
"""

from typing import Annotated

from pydantic import BaseModel, Field

from app.schemas.common import IdRef, RepoId, StrictBool, StrictInt

NonEmptyStr = Annotated[str, Field(min_length=1)]


class KgBuildIn(BaseModel):
    # strict=True：拒绝 bool 冒充 int（fuzz 实证 {"repo_id": false} 会被松散校验吞成 0 →
    # 静默回退到最新仓库并触发全量构建）；同时约束 int32 范围防 PG 溢出。
    repo_id: int = Field(default=0, ge=0, le=2147483647, strict=True)


class KgDocumentCreateIn(BaseModel):
    title: NonEmptyStr
    content: NonEmptyStr
    repo_id: RepoId = 0
    workspace: str = ""


class KgSearchIn(BaseModel):
    query: NonEmptyStr
    mode: str = "mix"  # naive | local | global | hybrid | mix
    repo_id: RepoId = 0
    workspace: str = ""
    top_k: StrictInt = 8
    websearch: StrictBool = False


class KgQueryStreamIn(KgSearchIn):
    use_cache: StrictBool = True


class KgEntityEditIn(BaseModel):
    name: NonEmptyStr
    new_name: str = ""  # 非空且 ≠ name 时走改名
    etype: str = ""
    description: str = ""
    repo_id: RepoId = 0
    workspace: str = ""


class KgEntityMergeIn(BaseModel):
    into: NonEmptyStr
    sources: Annotated[list[NonEmptyStr], Field(min_length=1)]
    repo_id: RepoId = 0
    workspace: str = ""


class KgRelationEditIn(BaseModel):
    id: IdRef
    rtype: str = ""
    description: str = ""
    repo_id: RepoId = 0
    workspace: str = ""


class KgCommunitiesBuildIn(BaseModel):
    repo_id: int = Field(default=0, ge=0, le=2147483647, strict=True)
    workspace: str = ""
    llm: bool = Field(default=True, strict=True)  # strict：拒绝 0/1 冒充 bool


class KgSettingsIn(BaseModel):
    settings: dict = {}
