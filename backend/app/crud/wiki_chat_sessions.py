"""wiki_chat_sessions 表 CRUD（CRUDBase 标准封装，领域特有查询在此追加）。"""

from app.crud.base import CRUDBase
from app.models.wiki_chat_sessions import WikiChatSession

crud_wiki_chat_sessions: CRUDBase[WikiChatSession, dict, dict] = CRUDBase(WikiChatSession)
