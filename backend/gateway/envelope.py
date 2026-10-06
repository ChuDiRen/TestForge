"""统一响应封套 {code, message, data} + 业务异常。"""

from typing import Any

from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, code: int, message: str, http_status: int = 400) -> None:
        self.code = code
        self.message = message
        self.http_status = http_status


def ok(data: Any = None, message: str = "ok") -> dict:
    return {"code": 0, "message": message, "data": data}


def err(code: int, message: str, http_status: int = 400) -> JSONResponse:
    return JSONResponse(status_code=http_status, content={"code": code, "message": message, "data": None})
