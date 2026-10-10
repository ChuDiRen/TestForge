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
    stale: Mapped[bool] = mapped_column(Boolean, default=False)  # 目标函数源码已变更，待回归确认
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
    suggestion: Mapped[str] = mapped_column(Text, default="")  # AI 修复建议（Markdown，DeepSeek 基于真实失败日志生成）
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


class Jobs(Base):
    """任务队列：持久化任务（生成/回归），gateway 进程内 worker 消费，崩溃后 running 重排队。"""

    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # JOB-xxxx
    kind: Mapped[str] = mapped_column(String(32), index=True)  # generate|regression
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)  # queued|running|done|failed
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    result_json: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    gen_code: Mapped[str] = mapped_column(String(64), default="")  # 关联生成（generate 类）
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Users(Base):
    """平台账号：admin 全权；viewer 只读（GET）。disabled 停用后登录/请求一律拒绝。"""

    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256), default="")  # PBKDF2-SHA256 salt$digest
    role: Mapped[str] = mapped_column(String(16), default="viewer")  # admin|viewer
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)  # 停用账号（离职/临时封禁）
    must_change: Mapped[bool] = mapped_column(Boolean, default=False)  # 待强制改密（默认口令/管理员重置后）
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class LlmCache(Base):
    """LLM 抽取缓存：内容 hash 键控，增量重建只对变更输入重付 token。"""

    __tablename__ = "llm_cache"
    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256(role|model|system|prompt)
    role: Mapped[str] = mapped_column(String(16), default="extract")  # extract|query|keyword
    model: Mapped[str] = mapped_column(String(64), default="")
    prompt: Mapped[str] = mapped_column(Text, default="")  # 截断留存（审计用）
    response: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class FnImpact(Base):
    """索引期预计算影响面（blast radius）：反向可达集 + 调用深度 + 置信度。"""

    __tablename__ = "fn_impacts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[int] = mapped_column(Integer, index=True)
    fn_name: Mapped[str] = mapped_column(String(256), index=True)
    callers_count: Mapped[int] = mapped_column(Integer, default=0)  # 直接调用方数
    callees_count: Mapped[int] = mapped_column(Integer, default=0)  # 直接依赖数
    reach_count: Mapped[int] = mapped_column(Integer, default=0)  # 反向可达集大小（改它炸多大）
    depth_reached: Mapped[int] = mapped_column(Integer, default=0)  # 反向传播最深层
    score: Mapped[float] = mapped_column(Float, default=0.0)  # 影响分（可达集/全函数归一）
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    __table_args__ = (Index("ix_fn_impact_repo_fn", "repo_id", "fn_name", unique=True),)


class FnCluster(Base):
    """功能聚类（Louvain 社区检测）：自动划分测试域。"""

    __tablename__ = "fn_clusters"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[int] = mapped_column(Integer, index=True)
    fn_name: Mapped[str] = mapped_column(String(256), index=True)
    cluster_id: Mapped[int] = mapped_column(Integer, default=0)
    label: Mapped[str] = mapped_column(String(128), default="")  # 测试域名（主导模块前缀/枢纽函数）
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    __table_args__ = (Index("ix_fn_cluster_repo_fn", "repo_id", "fn_name", unique=True),)


class KgEntity(Base):
    """文档级知识图谱实体（LightRAG 式）：从 wiki/需求/缺陷文本抽取。

    合并语义（LightRAG 三阶段对齐）：
    - etype_votes：type 投票计数 JSON {type: count}，多数票胜出；
    - desc_sources：各来源文档描述 JSON {source_ref: desc}，<8 条直接拼接、≥8 条 LLM 摘要；
    - source_refs：来源文档引用（选择性删除依据）。
    """

    __tablename__ = "kg_entities"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[int] = mapped_column(Integer, index=True, default=0)
    workspace: Mapped[str] = mapped_column(String(128), default="", index=True)  # 空=repo 作用域旧区
    name: Mapped[str] = mapped_column(String(256), index=True)
    etype: Mapped[str] = mapped_column(String(64), default="concept")  # module|function|concept|defect_pattern|...
    etype_votes: Mapped[str] = mapped_column(Text, default="{}")  # JSON {type: count}（type 投票）
    description: Mapped[str] = mapped_column(Text, default="")
    desc_sources: Mapped[str] = mapped_column(Text, default="{}")  # JSON {source_ref: description}
    source_refs: Mapped[str] = mapped_column(Text, default="[]")  # JSON array：来源文档引用（选择性删除依据）
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    __table_args__ = (Index("ix_kg_entity_repo_name", "repo_id", "name", unique=True),)


class KgRelation(Base):
    """知识图谱关系边：src/dst 为 KgEntity.name；weight=证据计数（支持该边的文档数）。"""

    __tablename__ = "kg_relations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[int] = mapped_column(Integer, index=True, default=0)
    workspace: Mapped[str] = mapped_column(String(128), default="", index=True)  # 空=repo 作用域旧区
    src_name: Mapped[str] = mapped_column(String(256), index=True)
    dst_name: Mapped[str] = mapped_column(String(256), index=True)
    rtype: Mapped[str] = mapped_column(String(64), default="related")
    description: Mapped[str] = mapped_column(Text, default="")
    desc_sources: Mapped[str] = mapped_column(Text, default="{}")  # JSON {source_ref: description}
    weight: Mapped[float] = mapped_column(Float, default=1.0)  # 证据计数（source_refs 长度）
    source_ref: Mapped[str] = mapped_column(String(256), default="")  # 首个来源（兼容旧读方）
    source_refs: Mapped[str] = mapped_column(Text, default="[]")  # JSON array：全部证据来源
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class DocStatus(Base):
    """知识摄入文档状态跟踪（LightRAG 异步状态机：pending → processing → ok/failed）。

    legacy kind（index/wiki/kg/fts/rag/user_doc）保持同步 ok|failed|stale 写入；
    kind='kg_doc' 为 LightRAG 式异步摄入管线（工作台分块→抽取→合并→索引）专用。
    """

    __tablename__ = "doc_status"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[int] = mapped_column(Integer, index=True, default=0)
    workspace: Mapped[str] = mapped_column(String(128), default="", index=True)  # 空=repo 作用域旧区
    kind: Mapped[str] = mapped_column(String(32), index=True)  # index|wiki|kg|fts|rag|kg_doc
    doc_key: Mapped[str] = mapped_column(String(512), default="")
    status: Mapped[str] = mapped_column(String(16), default="ok", index=True)  # pending|processing|ok|failed|stale
    error: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[str] = mapped_column(Text, default="")  # JSON：chunks/extract/strategy/file 等进度元数据
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    __table_args__ = (Index("ix_doc_status_key", "repo_id", "kind", "doc_key", unique=True),)


class KgCommunity(Base):
    """KG 社区（Louvain）+ 社区报告：map-reduce LLM 摘要，global 查询的主题级弹药库。"""

    __tablename__ = "kg_communities"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[int] = mapped_column(Integer, index=True, default=0)
    workspace: Mapped[str] = mapped_column(String(128), default="", index=True)
    level: Mapped[int] = mapped_column(Integer, default=1, index=True)  # 1=基础社区 2=社区 的社区（reduce 层）
    cluster_id: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(256), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    members: Mapped[str] = mapped_column(Text, default="[]")  # JSON array：成员实体名
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    summary_source: Mapped[str] = mapped_column(String(16), default="llm")  # llm|concat（无 Key 时的确定性回退）
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    __table_args__ = (Index("ix_kg_comm_scope", "repo_id", "workspace", "level", "cluster_id"),)


class KgExtraction(Base):
    """每份源文档的抽取结果留存（LightRAG 删除文档→用缓存重建图谱描述的依据）。"""

    __tablename__ = "kg_extractions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[int] = mapped_column(Integer, index=True, default=0)
    workspace: Mapped[str] = mapped_column(String(128), default="", index=True)
    source_ref: Mapped[str] = mapped_column(String(256), index=True)  # 文档引用（kgdoc:xx / wiki:1 / defect:BUG-x）
    doc_key: Mapped[str] = mapped_column(String(512), default="")  # rag_documents 主文档 doc_key（kg_doc 管线用）
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    result_json: Mapped[str] = mapped_column(Text, default="{}")  # 全 chunk 合并后的抽取结果
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    gleaning_rounds: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    __table_args__ = (Index("ix_kg_ext_scope_ref", "repo_id", "workspace", "source_ref"),)


class KgSetting(Base):
    """运行时检索参数覆盖（设置页可改，优先于 config .env 默认值）。"""

    __tablename__ = "kg_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")  # JSON 标量
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class EmbeddingCache(Base):
    """神经 embedding 持久缓存：同 (backend, model, text) 只付一次钱，重启不失效。"""

    __tablename__ = "embedding_cache"
    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256(backend|model|text)
    model: Mapped[str] = mapped_column(String(128), default="")
    vec: Mapped[str] = mapped_column(Text, default="")  # JSON array[float]
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ChatThread(Base):
    """AI 助手会话（对标 GitNexus 对话：多轮代码问答，绑定仓库范围）。"""

    __tablename__ = "chat_threads"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(128), default="新对话")
    repo_id: Mapped[int] = mapped_column(Integer, default=0)  # 0=不限仓库
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class ChatMessage(Base):
    """AI 助手消息：role=user/assistant；工具调用过程与引用溯源以 JSON 留存。"""

    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[int] = mapped_column(Integer, index=True)
    role: Mapped[str] = mapped_column(String(16))  # user|assistant
    content: Mapped[str] = mapped_column(Text, default="")
    tool_events: Mapped[str] = mapped_column(Text, default="[]")  # [{name,args,summary,ms}]
    citations: Mapped[str] = mapped_column(Text, default="[]")  # [[path:12-34]] 解析结果
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class WikiChatSession(Base):
    """Wiki 问答会话（OpenWiki wiki_chat_sessions 对等物）：按仓库分组的多会话。"""

    __tablename__ = "wiki_chat_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[int] = mapped_column(Integer, default=0, index=True)
    title: Mapped[str] = mapped_column(String(256), default="新问答")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class WikiChatMessage(Base):
    """Wiki 问答消息：sources 存来源页（供追问指代解析与 qa_reference 边），
    source_mode = knowledge_base|no_data（no_data 服务端禁止存为知识文档）。"""

    __tablename__ = "wiki_chat_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(Integer, index=True)
    role: Mapped[str] = mapped_column(String(16))  # user|assistant
    content: Mapped[str] = mapped_column(Text, default="")
    sources_json: Mapped[str] = mapped_column(Text, default="[]")  # [{id,title,score}]
    source_mode: Mapped[str] = mapped_column(String(24), default="knowledge_base")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
