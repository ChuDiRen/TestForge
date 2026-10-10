"""kg_settings 表 CRUD（CRUDBase 标准封装，领域特有查询在此追加）。"""

from app.crud.base import CRUDBase
from app.models.kg_settings import KgSetting

crud_kg_settings: CRUDBase[KgSetting, dict, dict] = CRUDBase(KgSetting)
