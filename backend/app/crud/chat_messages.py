"""chat_messages 表 CRUD（CRUDBase 标准封装，领域特有查询在此追加）。"""

from app.crud.base import CRUDBase
from app.models.chat_messages import ChatMessage

crud_chat_messages: CRUDBase[ChatMessage, dict, dict] = CRUDBase(ChatMessage)
