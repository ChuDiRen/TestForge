"""知识图谱域（KG/LightRAG）API 请求模型。

/workspace+repo_id 由各路由的 _resolve_ws 统一解析；query/q 双键是
kg 检索的兼容别名（query or q），model_dump 后两个键都在，原 or 链保持不变。
"""

from pydantic import BaseModel


class KgBuildIn(BaseModel):
    repo_id: int = 0


class KgDocumentCreateIn(BaseModel):
    title: str = ""
    content: str = ""
    repo_id: int = 0
    workspace: str = ""


class KgSearchIn(BaseModel):
    query: str = ""
    q: str = ""  # query 的兼容别名
    mode: str = "mix"  # naive | local | global | hybrid | mix
    repo_id: int = 0
    workspace: str = ""
    top_k: int = 8
    websearch: bool = False


class KgQueryStreamIn(KgSearchIn):
    use_cache: bool = True


class KgEntityEditIn(BaseModel):
    name: str = ""
    new_name: str = ""  # 非空且 ≠ name 时走改名
    etype: str = ""
    description: str = ""
    repo_id: int = 0
    workspace: str = ""


class KgEntityMergeIn(BaseModel):
    into: str = ""
    sources: list[str] = []
    repo_id: int = 0
    workspace: str = ""


class KgRelationEditIn(BaseModel):
    id: int = 0
    rtype: str = ""
    description: str = ""
    repo_id: int = 0
    workspace: str = ""


class KgCommunitiesBuildIn(BaseModel):
    repo_id: int = 0
    workspace: str = ""
    llm: bool = True


class KgSettingsIn(BaseModel):
    settings: dict = {}
