"""kg_relations 表 CRUD（CRUDBase 标准封装，领域特有查询在此追加）。"""

from app.crud.base import CRUDBase
from app.models.kg_relations import KgRelation

crud_kg_relations: CRUDBase[KgRelation, dict, dict] = CRUDBase(KgRelation)
