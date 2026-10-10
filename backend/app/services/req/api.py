"""req-svc 平铺 API：需求四步解析管线（原 gRPC ReqIngest.Parse 契约）。"""

from app.core import health
from app.core.trace import emit


def ping() -> dict:
    return health.ping("req-svc")


def parse(code: str, title: str, body: str, source: str = "paste", repo_id: int = 0, trace_id: str = "") -> dict:
    """四步管线：文档解析 → 规则抽取 → Wiki diff/冲突检测 → 编排建议。

    返回 ParseReport 形状：{req_code, story, rules[], conflict, conflict_detail,
    testability, status, pipeline, new_rules}；可测性 <80 status=已打回。
    """
    from app.services.req import parser

    res = parser.parse(code, title, body, source, repo_id)
    emit(
        "需求",
        "req-svc",
        f"需求解析 {code}: 规则 {len(res.rules)} 条 冲突={res.conflict} 可测性={res.testability:.0f}",
        req_code=code,
        trace_id=trace_id or None,
    )
    return {
        "req_code": code,
        "story": res.story,
        "rules": [{"rule": r.text, "change": r.change, "evidence": r.evidence} for r in res.rules],
        "conflict": res.conflict,
        "conflict_detail": res.conflict_detail,
        "testability": res.testability,
        "status": "待人审" if res.testability >= 80 else "已打回",
        "pipeline": res.pipeline,
        "new_rules": res.new_rules_for_wiki,
    }
