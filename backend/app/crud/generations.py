"""generations 表 CRUD（CRUDBase 标准封装，领域特有查询在此追加）。"""

from app.crud.base import CRUDBase
from app.models.generations import Generations

crud_generations: CRUDBase[Generations, dict, dict] = CRUDBase(Generations)
