"""生成域 API 请求模型（单目标 / 批量生成入队）。"""

from pydantic import BaseModel


class GenerationCreateIn(BaseModel):
    function: str = ""
    repo_id: int = 0
    layer: str = "ut"
    source_req: str = ""
    trace_id: str = ""


class GenerationBatchIn(BaseModel):
    repo_id: int = 0
    module: str = ""
    names: list[str] = []
    layer: str = "ut"
