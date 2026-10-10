"""数据契约层（schemas）：跨层共享的 pydantic 结构化模型。

与 models/（SQLAlchemy ORM 表）分离：本层承载 LLM 输出契约与 API 请求体模型，
不落库、不做表映射——FastAPI 分层骨架的 schemas/ 职责。
"""
