"""functions 表 CRUD（CRUDBase 标准封装，领域特有查询在此追加）。"""

from app.crud.base import CRUDBase
from app.models.functions import Functions

crud_functions: CRUDBase[Functions, dict, dict] = CRUDBase(Functions)
