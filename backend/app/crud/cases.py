"""cases 表 CRUD（CRUDBase 标准封装，领域特有查询在此追加）。"""

from app.crud.base import CRUDBase
from app.models.cases import Cases

crud_cases: CRUDBase[Cases, dict, dict] = CRUDBase(Cases)
