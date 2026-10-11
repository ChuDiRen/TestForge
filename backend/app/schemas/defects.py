"""缺陷域 API 请求模型（建缺陷 / 通用流转更新 / 评论）。"""

from pydantic import BaseModel


class DefectCreateIn(BaseModel):
    run_id: str = ""
    origin_run: str = ""  # run_id 的别名（runner-svc 调用形态，二选一）
    case_codes: list[str] = []
    req_code: str = ""
    trace_id: str = ""
    reason: str = "人工报障"
    title: str = ""  # 人工报障可给标题；自动建单缺省时由服务端用 目标：原因 组装


class DefectUpdateIn(BaseModel):
    """通用流转更新（Jira transition screen 语义）：状态转换 + 伴随字段一次提交。

    status 为空表示不改状态、只改字段；resolution 在转入"已关闭"时必填（服务端校验）；
    dupe_of 设置时服务端强制 resolution=重复 并把指向扁平化到重复链最终目标；
    expected_updated_at 为乐观锁基准（列表返回的 updated_at），不匹配返回 409。
    """

    status: str = ""
    resolution: str = ""
    priority: str = ""  # P0 | P1 | P2 | P3
    assignee: str = ""
    fix_version: str = ""
    dupe_of: str = ""  # 目标缺陷编码 BUG-xxx
    comment: str = ""  # 伴随评论（require_comment 边必填）
    actor: str = "qa"
    expected_updated_at: str = ""


class DefectCommentIn(BaseModel):
    body: str
    actor: str = "qa"
