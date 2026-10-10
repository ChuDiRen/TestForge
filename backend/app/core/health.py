"""服务健康探测（原 gRPC Pong 的单体等价物）：全部服务同进程，db_ok 共享同一数据库。"""

from app.core.config import VERSION


def ping(service: str) -> dict:
    from app.db.session import db_ok

    return {"service": service, "version": VERSION, "db_ok": db_ok()}
