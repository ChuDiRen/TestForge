"""请求模型共用严格类型。

pydantic 宽松模式会把 false 收编成 0、0 收编成 False、"1" 收编成 1——JSON Schema
类型声明（integer/boolean）与实际校验不一致，契约测试会把「接受非法输入」当缺陷报。
统一走 strict，让 schema 说的是真的。
"""

from typing import Annotated

from pydantic import Field

StrictBool = Annotated[bool, Field(strict=True)]
StrictFloat = Annotated[float, Field(strict=True)]
StrictInt = Annotated[int, Field(strict=True)]
# 引用型 id（线程/关系/指定仓库）：必须 ≥1，0 视为缺参由 422 表达
IdRef = Annotated[int, Field(strict=True, ge=1, le=2147483647)]
# 仓库选择：0 = 未指定（服务端兜底默认仓库），负数/超 int4 一律 422
RepoId = Annotated[int, Field(strict=True, ge=0, le=2147483647)]
