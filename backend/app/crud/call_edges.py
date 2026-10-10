"""call_edges 表 CRUD（CRUDBase 标准封装，领域特有查询在此追加）。"""

from app.crud.base import CRUDBase
from app.models.call_edges import CallEdges

crud_call_edges: CRUDBase[CallEdges, dict, dict] = CRUDBase(CallEdges)
