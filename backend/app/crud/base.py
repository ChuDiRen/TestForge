"""CRUD 通用基类（FastAPI 标准件）：泛型封装增删改查，具体模型继承后补充领域查询。

用法：
    crud_repos = CRUDBase(Repos)
    crud_repos.get(db, 1)                       # 主键取单条
    crud_repos.get_multi(db, skip=0, limit=50)  # 分页列表
    crud_repos.create(db, obj_in={...})         # dict 或 pydantic 模型均可
    crud_repos.update(db, db_obj=obj, obj_in={...})
    crud_repos.remove(db, id=1)
"""

from typing import Any, Generic, TypeVar, cast

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base_class import Base

ModelType = TypeVar("ModelType", bound=Base)
# 无界 TypeVar：允许 dict 或 pydantic 模型实例化（标准模板在有 In/Out 模型后可收紧 bound=BaseModel）
CreateSchemaType = TypeVar("CreateSchemaType")
UpdateSchemaType = TypeVar("UpdateSchemaType")


class CRUDBase(Generic[ModelType, CreateSchemaType, UpdateSchemaType]):
    """默认 CRUD（无预置写权限检查——鉴权由网关中间件承担）。"""

    def __init__(self, model: type[ModelType]) -> None:
        self.model = model

    def get(self, db: Session, id: Any) -> ModelType | None:
        return db.get(self.model, id)

    def get_multi(self, db: Session, *, skip: int = 0, limit: int = 100) -> list[ModelType]:
        return list(db.scalars(select(self.model).offset(skip).limit(limit)))

    def create(self, db: Session, *, obj_in: CreateSchemaType | dict[str, Any]) -> ModelType:
        data = obj_in.model_dump() if isinstance(obj_in, BaseModel) else dict(cast("dict[str, Any]", obj_in))
        db_obj = self.model(**data)
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def update(self, db: Session, *, db_obj: ModelType, obj_in: UpdateSchemaType | dict[str, Any]) -> ModelType:
        data = obj_in.model_dump(exclude_unset=True) if isinstance(obj_in, BaseModel) else dict(cast("dict[str, Any]", obj_in))
        for field, value in data.items():
            setattr(db_obj, field, value)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def remove(self, db: Session, *, id: Any) -> ModelType | None:
        obj = db.get(self.model, id)
        if obj is not None:
            db.delete(obj)
            db.commit()
        return obj
