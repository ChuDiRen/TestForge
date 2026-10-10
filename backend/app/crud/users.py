"""users 表 CRUD（CRUDBase 标准封装，领域特有查询在此追加）。"""

from app.crud.base import CRUDBase
from app.models.users import Users

crud_users: CRUDBase[Users, dict, dict] = CRUDBase(Users)
