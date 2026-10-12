"""知识域 API 请求模型（知识文档入库 / Wiki 问答 / 会话 / URL 导入）。"""

from pydantic import BaseModel, Field

from app.schemas.common import RepoId, StrictBool


class SourceRef(BaseModel):
    """qa_reference 边数据源（wiki 页引用，{id,title}）。"""

    id: int = Field(strict=True, ge=1)
    title: str = Field(min_length=1)


class KnowledgeDocBody(BaseModel):
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    repo_id: RepoId = 0
    # 问答存档场景（OpenWiki save_message_as_page 移植）：kind_hint="qa" 时必须带
    # 知识库来源，否则 422 拒绝——反幻觉门卫在服务端强制，不再只靠前端
    kind_hint: str = ""  # "" | "qa"
    sources: list[SourceRef] = []  # 来源 wiki 页（qa_reference 边数据源）
    assess: StrictBool = True  # 评估门卫（OpenWiki assess_content 移植）


class WikiAskBody(BaseModel):
    question: str = Field(min_length=1)
    repo_id: RepoId = 0
    history: str = ""
    session_id: RepoId = 0  # >0 时持久化到 wiki_chat_sessions/messages（OpenWiki wiki_chat 对等物）


class WikiChatSessionBody(BaseModel):
    repo_id: RepoId = 0
    title: str = "新问答"


class WikiRebuildBody(BaseModel):
    """一键重建请求体（原先路由层裸读 json_body，schema 未声明导致空 body 契约误报）。"""

    repo_id: RepoId = 0
    full: StrictBool = False


class ImportUrlBody(BaseModel):
    # 仅支持 http(s) 抓取（urllib 通道），语法层拦截非法 URL（原业务 400）
    url: str = Field(min_length=1, max_length=2048, pattern=r"^https?://\S+")
    repo_id: RepoId = 0
    title: str = ""
