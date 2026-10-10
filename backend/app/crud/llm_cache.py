"""llm_cache 表 CRUD（CRUDBase 标准封装，领域特有查询在此追加）。"""

from app.crud.base import CRUDBase
from app.models.llm_cache import LlmCache

crud_llm_cache: CRUDBase[LlmCache, dict, dict] = CRUDBase(LlmCache)
