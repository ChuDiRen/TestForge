"""知识域 API 请求模型（知识文档入库 / Wiki 问答 / 会话 / URL 导入）。"""

from pydantic import BaseModel


class KnowledgeDocBody(BaseModel):
    title: str
    content: str
    repo_id: int = 0
    # 问答存档场景（OpenWiki save_message_as_page 移植）：kind_hint="qa" 时必须带
    # 知识库来源，否则 422 拒绝——反幻觉门卫在服务端强制，不再只靠前端
    kind_hint: str = ""  # "" | "qa"
    sources: list[dict] = []  # [{id,title}] 来源 wiki 页（qa_reference 边数据源）
    assess: bool = True  # 评估门卫（OpenWiki assess_content 移植）


class WikiAskBody(BaseModel):
    question: str
    repo_id: int = 0
    history: str = ""
    session_id: int = 0  # >0 时持久化到 wiki_chat_sessions/messages（OpenWiki wiki_chat 对等物）


class WikiChatSessionBody(BaseModel):
    repo_id: int = 0
    title: str = "新问答"


class ImportUrlBody(BaseModel):
    url: str
    repo_id: int = 0
    title: str = ""
