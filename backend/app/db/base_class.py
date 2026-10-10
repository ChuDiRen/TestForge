"""声明式基类（FastAPI 标准件）：DeclarativeBase 类形式 + @declared_attr 自动生成 __tablename__。

类名驼峰 → 表名蛇形（Repos → repos）；模型显式声明 __tablename__ 时以显式为准。
必须用类形式而非 @as_declarative() 装饰器——mypy 的 SQLAlchemy 语义（含 **kwargs 构造器）
只识别 class Base(DeclarativeBase) 继承链。
"""

import re

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase, declared_attr

# 约束命名约定（Alembic 迁移友好的标准实践）
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def snake_case(name: str) -> str:
    """CamelCase → snake_case（Repos → repos，FnImpact → fn_impact）。"""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    @declared_attr.directive
    def __tablename__(cls) -> str:  # noqa: N805
        return snake_case(cls.__name__)
