"""runs 表 CRUD（CRUDBase 标准封装，领域特有查询在此追加）。"""

from app.crud.base import CRUDBase
from app.models.runs import Runs

crud_runs: CRUDBase[Runs, dict, dict] = CRUDBase(Runs)
