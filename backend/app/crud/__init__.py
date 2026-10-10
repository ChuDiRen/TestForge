"""CRUD 层：一模型一文件，统一由 CRUDBase 标准封装；路由经 Depends(get_db) 传入会话。"""

from app.crud.call_edges import crud_call_edges
from app.crud.cases import crud_cases
from app.crud.chat_messages import crud_chat_messages
from app.crud.chat_threads import crud_chat_threads
from app.crud.contract_diffs import crud_contract_diffs
from app.crud.contracts import crud_contracts
from app.crud.defects import crud_defects
from app.crud.doc_status import crud_doc_status
from app.crud.embedding_cache import crud_embedding_cache
from app.crud.fn_clusters import crud_fn_clusters
from app.crud.fn_impacts import crud_fn_impacts
from app.crud.functions import crud_functions
from app.crud.generation_events import crud_generation_events
from app.crud.generations import crud_generations
from app.crud.iterations import crud_iterations
from app.crud.jobs import crud_jobs
from app.crud.kg_communities import crud_kg_communities
from app.crud.kg_entities import crud_kg_entities
from app.crud.kg_extractions import crud_kg_extractions
from app.crud.kg_relations import crud_kg_relations
from app.crud.kg_settings import crud_kg_settings
from app.crud.llm_cache import crud_llm_cache
from app.crud.repos import crud_repos
from app.crud.requirements import crud_requirements
from app.crud.runs import crud_runs
from app.crud.trace_events import crud_trace_events
from app.crud.users import crud_users
from app.crud.wiki_chat_messages import crud_wiki_chat_messages
from app.crud.wiki_chat_sessions import crud_wiki_chat_sessions
from app.crud.wiki_deps import crud_wiki_deps
from app.crud.wiki_pages import crud_wiki_pages

__all__ = [
    "crud_call_edges",
    "crud_cases",
    "crud_chat_messages",
    "crud_chat_threads",
    "crud_contract_diffs",
    "crud_contracts",
    "crud_defects",
    "crud_doc_status",
    "crud_embedding_cache",
    "crud_fn_clusters",
    "crud_fn_impacts",
    "crud_functions",
    "crud_generation_events",
    "crud_generations",
    "crud_iterations",
    "crud_jobs",
    "crud_kg_communities",
    "crud_kg_entities",
    "crud_kg_extractions",
    "crud_kg_relations",
    "crud_kg_settings",
    "crud_llm_cache",
    "crud_repos",
    "crud_requirements",
    "crud_runs",
    "crud_trace_events",
    "crud_users",
    "crud_wiki_chat_messages",
    "crud_wiki_chat_sessions",
    "crud_wiki_deps",
    "crud_wiki_pages",
]
