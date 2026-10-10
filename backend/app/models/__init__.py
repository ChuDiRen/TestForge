"""SQLAlchemy 数据模型（一表一文件，此处统一注册保证 create_all 可见）。"""

from app.models.call_edges import CallEdges
from app.models.cases import Cases
from app.models.chat_messages import ChatMessage
from app.models.chat_threads import ChatThread
from app.models.contract_diffs import ContractDiffs
from app.models.contracts import Contracts
from app.models.defects import Defects
from app.models.doc_status import DocStatus
from app.models.embedding_cache import EmbeddingCache
from app.models.fn_clusters import FnCluster
from app.models.fn_impacts import FnImpact
from app.models.functions import Functions
from app.models.generation_events import GenerationEvents
from app.models.generations import Generations
from app.models.iterations import Iterations
from app.models.jobs import Jobs
from app.models.kg_communities import KgCommunity
from app.models.kg_entities import KgEntity
from app.models.kg_extractions import KgExtraction
from app.models.kg_relations import KgRelation
from app.models.kg_settings import KgSetting
from app.models.llm_cache import LlmCache
from app.models.repos import Repos
from app.models.requirements import Requirements
from app.models.runs import Runs
from app.models.trace_events import TraceEvents
from app.models.users import Users
from app.models.wiki_chat_messages import WikiChatMessage
from app.models.wiki_chat_sessions import WikiChatSession
from app.models.wiki_deps import WikiDeps
from app.models.wiki_pages import WikiPages

__all__ = [
    "Repos",
    "Functions",
    "CallEdges",
    "WikiPages",
    "WikiDeps",
    "Contracts",
    "ContractDiffs",
    "Requirements",
    "Cases",
    "Runs",
    "Defects",
    "Iterations",
    "TraceEvents",
    "Generations",
    "GenerationEvents",
    "Jobs",
    "Users",
    "LlmCache",
    "FnImpact",
    "FnCluster",
    "KgEntity",
    "KgRelation",
    "DocStatus",
    "KgCommunity",
    "KgExtraction",
    "KgSetting",
    "EmbeddingCache",
    "ChatThread",
    "ChatMessage",
    "WikiChatSession",
    "WikiChatMessage",
]
