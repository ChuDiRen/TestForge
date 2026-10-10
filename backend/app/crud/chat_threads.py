"""chat_threads 表 CRUD（CRUDBase 标准封装，领域特有查询在此追加）。"""

from app.crud.base import CRUDBase
from app.models.chat_threads import ChatThread

crud_chat_threads: CRUDBase[ChatThread, dict, dict] = CRUDBase(ChatThread)
