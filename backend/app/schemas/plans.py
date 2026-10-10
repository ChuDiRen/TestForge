"""迭代计划域 API 请求模型（建迭代）。"""

from pydantic import BaseModel


class PlanCreateIn(BaseModel):
    version: str = ""
    req_codes: list[str] = []
