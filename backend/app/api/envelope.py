"""统一响应封套 {code, message, data} + 业务异常。"""

from typing import Any

from fastapi import Request
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


async def json_body(request: Request) -> dict:
    """裸解析 JSON body 的端点统一走这里：空/非法/非对象输入返回 400 而不是 500。"""
    try:
        body = await request.json()
    except Exception:
        raise ApiError(400, "body 需为合法 JSON", 400) from None
    if not isinstance(body, dict):
        raise ApiError(400, "body 需为 JSON 对象", 400)
    return body
