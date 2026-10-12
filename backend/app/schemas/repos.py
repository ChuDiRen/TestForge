"""仓库域 API 请求模型（接入仓库）。"""

from pydantic import BaseModel, Field


class RepoCreateIn(BaseModel):
    # URL 语法（远程 git / 本地路径在 allow_local 模式）由服务端 validate_remote_url 校验，
    # schema 只约束非空与列宽，避免把部署开关编死进契约。
    url: str = Field(min_length=1, max_length=512)
    branch: str = "main"
    credential_ref: str = ""
    webhook: bool = False
