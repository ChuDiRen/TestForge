"""生成域 API 请求模型（单目标 / 批量生成入队）。"""

from typing import Annotated

from pydantic import BaseModel, Field

from app.schemas.common import IdRef, RepoId

NonEmptyStr = Annotated[str, Field(min_length=1)]


class GenerationCreateIn(BaseModel):
    function: NonEmptyStr
    repo_id: RepoId = 0
    layer: str = "ut"
    source_req: str = ""
    trace_id: str = ""


class GenerationBatchIn(BaseModel):
    repo_id: IdRef  # 批量生成必须指定仓库（原路由层 400「repo_id 必填」）
    module: str = ""
    names: Annotated[list[NonEmptyStr], Field(min_length=1)]
    layer: str = "ut"
