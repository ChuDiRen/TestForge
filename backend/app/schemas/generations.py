"""生成域 API 请求模型（单目标 / 批量生成入队）。"""

from pydantic import BaseModel


class GenerationCreateIn(BaseModel):
    function: str = ""
    target: str = ""  # function 的兼容别名（new_generation 二选一）
    repo_id: int = 0
    layer: str = "ut"
    source_req: str = ""
    trace_id: str = ""


class GenerationBatchIn(BaseModel):
    repo_id: int = 0
    module: str = ""
    names: list[str] = []
    layer: str = "ut"
