"""核心数据模型（PROMPT §6，字段可增不可减）。"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from services.shared.db import Base


def _now() -> datetime:
    return datetime.now()


class Repos(Base):
    __tablename__ = "repos"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String(512))
    branch: Mapped[str] = mapped_column(String(128), default="main")
    credential_ref: Mapped[str] = mapped_column(String(256), default="")  # 凭据引用，不落明文
    last_pull: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="已接入")
    local_path: Mapped[str] = mapped_column(String(512), default="")
    head_rev: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Functions(Base):
    __tablename__ = "functions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[int] = mapped_column(Integer, ForeignKey("repos.id"), index=True)
    module: Mapped[str] = mapped_column(String(256), index=True)
    name: Mapped[str] = mapped_column(String(256), index=True)
    signature: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(Text, default="")
    file: Mapped[str] = mapped_column(String(512), default="")
    line: Mapped[int] = mapped_column(Integer, default=0)
    docstring: Mapped[str] = mapped_column(Text, default="")
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)
    language: Mapped[str] = mapped_column(String(32), default="")  # python|go|java|...（多语言索引）


class CallEdges(Base):
    __tablename__ = "call_edges"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    caller_id: Mapped[int] = mapped_column(Integer, ForeignKey("functions.id"), index=True)
    callee_id: Mapped[int] = mapped_column(Integer, ForeignKey("functions.id"), index=True)


class WikiPages(Base):
    __tablename__ = "wiki_pages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[int] = mapped_column(Integer, ForeignKey("repos.id"), index=True)
    level: Mapped[str] = mapped_column(String(16), index=True)  # repo|module|function|system
    title: Mapped[str] = mapped_column(String(256))
    content_md: Mapped[str] = mapped_column(Text, default="")
    rev: Mapped[int] = mapped_column(Integer, default=1)
    stale: Mapped[bool] = mapped_column(Boolean, default=False)
    module: Mapped[str] = mapped_column(String(256), default="", index=True)
    function: Mapped[str] = mapped_column(String(256), default="", index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class WikiDeps(Base):
    __tablename__ = "wiki_deps"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    page_id: Mapped[int] = mapped_column(Integer, ForeignKey("wiki_pages.id"), index=True)
    depends_on_page_id: Mapped[int] = mapped_column(Integer, ForeignKey("wiki_pages.id"))


class Contracts(Base):
    __tablename__ = "contracts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), index=True)
    type: Mapped[str] = mapped_column(String(16))  # rest|grpc|topic
    provider_repo: Mapped[str] = mapped_column(String(256), default="")
    version: Mapped[str] = mapped_column(String(64), default="v1.0.0")
    spec: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="active")
    consumers: Mapped[str] = mapped_column(Text, default="")  # JSON array
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ContractDiffs(Base):
    __tablename__ = "contract_diffs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = mapped_column(Integer, ForeignKey("contracts.id"), index=True)
    from_v: Mapped[str] = mapped_column(String(64))
    to_v: Mapped[str] = mapped_column(String(64))
    breaking: Mapped[bool] = mapped_column(Boolean, default=False)
    detail: Mapped[str] = mapped_column(Text, default="")  # JSON
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Requirements(Base):
    __tablename__ = "requirements"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(512))
    source: Mapped[str] = mapped_column(String(64), default="paste")  # md|docx|pdf|feishu|confluence|paste
    body: Mapped[str] = mapped_column(Text, default="")
    repo_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("repos.id"), nullable=True)
    testability_score: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32), default="解析中", index=True)
    trace_id: Mapped[str] = mapped_column(String(64), default="")
    parse_report: Mapped[str] = mapped_column(Text, default="")  # JSON
    quality_profile: Mapped[str] = mapped_column(Text, default="")  # JSON：G0~G6 档案
    manual_interventions: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Cases(Base):
    __tablename__ = "cases"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    layer: Mapped[str] = mapped_column(String(16), index=True)  # ut|api|fn|e2e|contract
    title: Mapped[str] = mapped_column(String(512))
    module: Mapped[str] = mapped_column(String(256), default="", index=True)
    category: Mapped[str] = mapped_column(String(32), default="normal", index=True)  # normal|boundary|exception|permission|contract
    schema_json: Mapped[str] = mapped_column(Text, default="")
    source_req: Mapped[str] = mapped_column(String(64), default="", index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.9)
    status: Mapped[str] = mapped_column(String(32), default="草稿", index=True)  # 草稿|待人审|已入库
    review_note: Mapped[str] = mapped_column(Text, default="")
    trace_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    gen_id: Mapped[str] = mapped_column(String(64), default="")
    last_run_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    target_function: Mapped[str] = mapped_column(String(256), default="")
    repo_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Runs(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # RUN-xxxx
    target: Mapped[str] = mapped_column(String(256))
    layer: Mapped[str] = mapped_column(String(16))
    trigger: Mapped[str] = mapped_column(String(32), default="手动")
    sandbox_status: Mapped[str] = mapped_column(String(32), default="pending")
    pass_total: Mapped[int] = mapped_column(Integer, default=0)
    pass_count: Mapped[int] = mapped_column(Integer, default=0)
    coverage: Mapped[float] = mapped_column(Float, default=0.0)
    repair_rounds: Mapped[int] = mapped_column(Integer, default=0)
    cost_s: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)
    trace_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    req_code: Mapped[str] = mapped_column(String(64), default="")
    gen_id: Mapped[str] = mapped_column(String(64), default="")
    log_json: Mapped[str] = mapped_column(Text, default="")  # 时间线/用例结果 JSON
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Defects(Base):
    __tablename__ = "defects"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # BUG-xxx
    title: Mapped[str] = mapped_column(String(512))
    origin_run: Mapped[str] = mapped_column(String(64), default="")
    case_codes: Mapped[str] = mapped_column(Text, default="[]")  # JSON array
    req_code: Mapped[str] = mapped_column(String(64), default="")
    severity: Mapped[str] = mapped_column(String(16), default="严重")
    status: Mapped[str] = mapped_column(String(32), default="新建", index=True)
    assignee: Mapped[str] = mapped_column(String(64), default="")
    trace_id: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Iterations(Base):
    __tablename__ = "iterations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # ITER-xxx
    version: Mapped[str] = mapped_column(String(64), default="")
    req_codes: Mapped[str] = mapped_column(Text, default="[]")  # JSON array
    case_stats: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    entry_status: Mapped[str] = mapped_column(String(32), default="待准入")
    exit_status: Mapped[str] = mapped_column(String(32), default="待准出")
    report: Mapped[str] = mapped_column(Text, default="")  # JSON
    trace_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class TraceEvents(Base):
    __tablename__ = "trace_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    type: Mapped[str] = mapped_column(String(16))  # 需求|生成|执行|仓库|契约|缺陷|计划
    actor: Mapped[str] = mapped_column(String(64), default="system")
    summary: Mapped[str] = mapped_column(Text, default="")
    req_code: Mapped[str] = mapped_column(String(64), default="", index=True)
    extra: Mapped[str] = mapped_column(Text, default="{}")


Index("ix_trace_events_trace", TraceEvents.trace_id, TraceEvents.ts)


class Generations(Base):
    __tablename__ = "generations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # GEN-xxxx
    repo_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_function: Mapped[str] = mapped_column(String(256), default="")
    layer: Mapped[str] = mapped_column(String(16), default="ut")
    status: Mapped[str] = mapped_column(String(32), default="running")
    trace_id: Mapped[str] = mapped_column(String(64), default="")
    req_code: Mapped[str] = mapped_column(String(64), default="")
    plan_json: Mapped[str] = mapped_column(Text, default="")
    codegen: Mapped[str] = mapped_column(Text, default="")  # 最终 pytest 代码
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class GenerationEvents(Base):
    __tablename__ = "generation_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gen_code: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="stage")  # stage|log|result
    stage: Mapped[str] = mapped_column(String(32), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    payload_json: Mapped[str] = mapped_column(Text, default="")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_now)
