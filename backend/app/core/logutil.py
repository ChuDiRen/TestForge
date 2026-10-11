"""结构化 JSON 日志 + trace_id 上下文。"""

import contextvars
import json
import logging
import sys
import time
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

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
    fmt = JsonFormatter()
    stdout = logging.StreamHandler(sys.stdout)
    stdout.setFormatter(fmt)
    # 文件日志逐条落盘（FileHandler 每条记录 flush）：进程被 shell 重定向/缓冲坑害时日志仍可靠，
    # 这是 uvicorn.log 停止增长事故的根治——不再依赖启动方式。
    log_dir = Path(__file__).resolve().parents[2] / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    fileh = RotatingFileHandler(log_dir / "app.log", maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8")
    fileh.setFormatter(fmt)
    root = logging.getLogger()
    root.handlers = [stdout, fileh]
    root.setLevel(level.upper())
    # 收敛 grpc/uvicorn 噪音
    for noisy in ("uvicorn.access", "uvicorn.error", "uvicorn"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger(service).setLevel(level.upper())
