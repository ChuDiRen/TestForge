"""embedding_cache 表 CRUD（CRUDBase 标准封装，领域特有查询在此追加）。"""

from app.crud.base import CRUDBase
from app.models.embedding_cache import EmbeddingCache

crud_embedding_cache: CRUDBase[EmbeddingCache, dict, dict] = CRUDBase(EmbeddingCache)
