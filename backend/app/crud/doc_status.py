"""doc_status 表 CRUD（CRUDBase 标准封装，领域特有查询在此追加）。"""

from app.crud.base import CRUDBase
from app.models.doc_status import DocStatus

crud_doc_status: CRUDBase[DocStatus, dict, dict] = CRUDBase(DocStatus)
