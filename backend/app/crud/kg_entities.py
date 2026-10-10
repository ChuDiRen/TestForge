"""kg_entities 表 CRUD（CRUDBase 标准封装，领域特有查询在此追加）。"""

from app.crud.base import CRUDBase
from app.models.kg_entities import KgEntity

crud_kg_entities: CRUDBase[KgEntity, dict, dict] = CRUDBase(KgEntity)
