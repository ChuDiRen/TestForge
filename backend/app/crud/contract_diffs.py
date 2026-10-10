"""contract_diffs 表 CRUD（CRUDBase 标准封装，领域特有查询在此追加）。"""

from app.crud.base import CRUDBase
from app.models.contract_diffs import ContractDiffs

crud_contract_diffs: CRUDBase[ContractDiffs, dict, dict] = CRUDBase(ContractDiffs)
