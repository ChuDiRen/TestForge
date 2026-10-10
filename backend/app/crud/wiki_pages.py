"""wiki_pages 表 CRUD（CRUDBase 标准封装，领域特有查询在此追加）。"""

from app.crud.base import CRUDBase
from app.models.wiki_pages import WikiPages

crud_wiki_pages: CRUDBase[WikiPages, dict, dict] = CRUDBase(WikiPages)
