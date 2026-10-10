"""wiki_deps 表 CRUD（CRUDBase 标准封装，领域特有查询在此追加）。"""

from app.crud.base import CRUDBase
from app.models.wiki_deps import WikiDeps

crud_wiki_deps: CRUDBase[WikiDeps, dict, dict] = CRUDBase(WikiDeps)
