"""kg_extractions 表 CRUD（CRUDBase 标准封装，领域特有查询在此追加）。"""

from app.crud.base import CRUDBase
from app.models.kg_extractions import KgExtraction

crud_kg_extractions: CRUDBase[KgExtraction, dict, dict] = CRUDBase(KgExtraction)
