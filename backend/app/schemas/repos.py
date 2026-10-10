"""仓库域 API 请求模型（接入仓库）。"""

from pydantic import BaseModel


class RepoCreateIn(BaseModel):
    url: str = ""
    branch: str = "main"
    credential_ref: str = ""
    webhook: bool = False
