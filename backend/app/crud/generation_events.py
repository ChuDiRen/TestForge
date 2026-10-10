"""generation_events 表 CRUD（CRUDBase 标准封装，领域特有查询在此追加）。"""

from app.crud.base import CRUDBase
from app.models.generation_events import GenerationEvents

crud_generation_events: CRUDBase[GenerationEvents, dict, dict] = CRUDBase(GenerationEvents)
