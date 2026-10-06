"""结构化 JSON 日志 + trace_id 上下文。"""

import contextvars
import json
import logging
import sys
import time
import uuid

trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="-")


def new_trace_id() -> str:
    return f"tr_{uuid.uuid4().hex[:12]}"


def set_trace_id(tid: str) -> None:
    trace_id_var.set(tid or "-")


def get_trace_id() -> str:
    return trace_id_var.get()


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "service": record.name,
            "trace_id": get_trace_id(),
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            payload["exc"] = repr(record.exc_info[1])
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(service: str, level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # 收敛 grpc/uvicorn 噪音
    for noisy in ("grpc", "uvicorn.access", "uvicorn.error", "uvicorn"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger(service).setLevel(level.upper())
