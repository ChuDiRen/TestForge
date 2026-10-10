"""fn_impacts 表 CRUD（CRUDBase 标准封装，领域特有查询在此追加）。"""

from app.crud.base import CRUDBase
from app.models.fn_impacts import FnImpact

crud_fn_impacts: CRUDBase[FnImpact, dict, dict] = CRUDBase(FnImpact)
