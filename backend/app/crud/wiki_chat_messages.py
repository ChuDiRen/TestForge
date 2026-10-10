"""wiki_chat_messages 表 CRUD（CRUDBase 标准封装，领域特有查询在此追加）。"""

from app.crud.base import CRUDBase
from app.models.wiki_chat_messages import WikiChatMessage

crud_wiki_chat_messages: CRUDBase[WikiChatMessage, dict, dict] = CRUDBase(WikiChatMessage)
