"""共享 Pong 构造 + 服务命名。"""

from services.shared.config import VERSION
from services.shared.gen import testforge_pb2 as pb2


def pong(service: str) -> "pb2.PongRes":
    from services.shared.db import db_ok

    return pb2.PongRes(service=service, version=VERSION, db_ok=db_ok())
