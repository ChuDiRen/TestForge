"""runner-svc 入口：gRPC TestRunner（沙箱执行 + 修复循环）。"""

import json
import logging
import uuid

import grpc

from services.shared.base import pong
from services.shared.config import GRPC_PORTS
from services.shared.gen import testforge_pb2 as pb2
from services.shared.gen import testforge_pb2_grpc as pb2_grpc
from services.shared.grpc_server import run_server
from services.shared.logutil import setup_logging

NAME = "runner-svc"
log = logging.getLogger(NAME)


class TestRunnerServicer(pb2_grpc.TestRunnerServicer):
    def Ping(self, request, context):  # noqa: N802
        return pong(NAME)

    def Execute(self, request, context):  # noqa: N802
        from services.runner_svc import service

        run_code = request.run_id or f"RUN-{uuid.uuid4().hex[:8]}"
        cases = [
            {
                "code": c.code,
                "title": c.title,
                "layer": c.layer,
                "module": c.module,
                "category": c.category,
                "schema_json": c.schema_json,
                "source_req": c.source_req,
            }
            for c in request.cases
        ]
        # 代码来源：TestSuite case.schema_json 顶层或嵌套 schema_json 字段中的 "code_file"
        source_code = ""
        for c in request.cases:
            try:
                data = json.loads(c.schema_json)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            cf = data.get("code_file")
            if not cf and isinstance(data.get("schema_json"), str):
                try:
                    cf = json.loads(data["schema_json"]).get("code_file")
                except json.JSONDecodeError:
                    cf = None
            if cf:
                source_code = cf
                break
        if not source_code:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "TestSuite 缺少 code_file")

        res = service.execute_suite(
            run_code=run_code,
            cases=cases,
            source_code=source_code,
            repo_id=request.repo_id,
            trace_id=request.trace_id,
            req_code=cases[0].get("source_req", "") if cases else "",
            # 回归场景：只执行关联用例（case code 尾段 TC-0NN → 函数名子串 tc0nn）
            only=_only_selectors(cases),
            target_function=request.target_function,
        )
        report = service.result_to_report(run_code, res)
        return pb2.RunReport(
            run_id=run_code,
            sandbox_status=report["sandbox_status"],
            pass_total=report["pass_total"],
            pass_count=report["pass_count"],
            coverage=report["coverage"],
            repair_rounds=report["repair_rounds"],
            cost_s=report["cost_s"],
            status=report["status"],
            log_json=report["log_json"],
        )


def _only_selectors(cases: list[dict]) -> list[str]:
    """从 case code（CASE-XXXXXX-TC-0NN）提取 pytest -k 选择子串；全量生成时为空。"""
    sel = []
    for c in cases:
        code = (c.get("code") or "").upper()
        if "-TC-" in code:
            sel.append("tc" + code.rsplit("-TC-", 1)[-1].replace("-", ""))
    return sel


def register(server) -> None:
    pb2_grpc.add_TestRunnerServicer_to_server(TestRunnerServicer(), server)


def main() -> None:
    setup_logging(NAME)
    log.info("starting %s", NAME)
    run_server(GRPC_PORTS[NAME], NAME, register)


if __name__ == "__main__":
    main()
