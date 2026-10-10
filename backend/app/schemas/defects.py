"""缺陷域 API 请求模型（建缺陷 / 状态流转）。"""

from pydantic import BaseModel


class DefectCreateIn(BaseModel):
    run_id: str = ""
    origin_run: str = ""  # run_id 的别名（runner-svc 调用形态，二选一）
    case_codes: list[str] = []
    req_code: str = ""
    trace_id: str = ""
    reason: str = "人工报障"


class DefectStatusIn(BaseModel):
    status: str = ""  # 新建 | 已确认 | 修复中 | 待回归 | 已关闭
    actor: str = "qa"
