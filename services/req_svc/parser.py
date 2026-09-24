"""req-svc 四步解析管线：① 文档解析 → ② 规则抽取 → ③ Wiki diff/冲突检测 → ④ 用例生成编排。

可测性评分（G0）：<80 自动打回产品。
mock 模式全部确定性：正则/关键词抽取 + 启发式评分。
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field

from pydantic import BaseModel

from services.shared.db import get_session
from services.shared.gen import testforge_pb2 as pb2
from services.shared.llm import LLMClient, mock_task
from services.shared.models import Functions, WikiPages

log = logging.getLogger("req-svc.parse")

_NUM_RULE = re.compile(r"(?:不超过|不少于|大于等于|小于等于|至少|最多|≥|≤|>|<|=\s*)?\s*(\d+)\s*(min|分钟|s|秒|ms|天|元|%|个|条)?")
_ROLE = re.compile(r"作为\s*([^，。,；;\n]{2,20})")
_AC = re.compile(r"(?:验收条件|AC)\s*[:：]?\s*(.+)", re.M)
_UNMEASURABLE = ("更好用", "更友好", "提升体验", "优化一下", "尽快", "更好", "易用")


@dataclass
class Rule:
    text: str
    change: str = "ADDED"  # ADDED|MODIFIED|UNCHANGED
    evidence: str = ""
    kind: str = "rule"  # story|rule|ac|permission|boundary

    def as_dict(self) -> dict:
        return {"rule": self.text, "change": self.change, "evidence": self.evidence, "kind": self.kind}


@dataclass
class ParseResult:
    story: str = ""
    rules: list[Rule] = field(default_factory=list)
    conflict: bool = False
    conflict_detail: str = ""
    testability: float = 0.0
    pipeline: list[str] = field(default_factory=list)
    new_rules_for_wiki: list[str] = field(default_factory=list)


# ---------------- ① 文档解析 ----------------


def parse_document(body: str, source: str) -> str:
    """md/粘贴直接用文本；docx 抽取段落。"""
    if source == "docx" or (body[:2] == "PK" if len(body) > 2 else False):
        try:
            import docx

            doc = docx.Document(io.BytesIO(body.encode("latin-1", errors="ignore"))) if isinstance(body, str) and body[:2] == "PK" else None
            if doc is not None:
                return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except Exception as exc:  # noqa: BLE001
            log.warning("docx parse failed: %s", exc)
    return body


# ---------------- ② 规则抽取 + 可测性评分 ----------------


def extract_rules(text: str) -> list[Rule]:
    rules: list[Rule] = []
    story = _ROLE.search(text)
    if story:
        rules.append(Rule(f"用户故事：作为{story.group(1)}", kind="story", evidence=story.group(0)))
    for m in _NUM_RULE.finditer(text):
        ctx = text[max(0, m.start() - 20) : m.end() + 10].strip()
        rules.append(Rule(f"数量/阈值约束：{ctx}", kind="boundary", evidence=m.group(0)))
    for m in _AC.finditer(text):
        rules.append(Rule(f"验收条件：{m.group(1).strip()[:80]}", kind="ac", evidence=m.group(1)[:80]))
    if re.search(r"权限|角色|越权|管理员|买家|风控", text):
        rules.append(Rule("角色权限约束存在", kind="permission", evidence="关键词命中"))
    base = [r for r in rules if r.kind != "story"]
    if not base:
        rules.append(Rule("待确认核心规则（未抽取到可验证约束）", kind="rule"))
    return rules


def testability_score(text: str, rules: list[Rule]) -> float:
    """G0 可测性评分（0~100，启发式，mock 确定性）。"""
    score = 40.0
    kinds = {r.kind for r in rules}
    if "ac" in kinds:
        score += 20
    if "boundary" in kinds:
        score += 20
    if "permission" in kinds:
        score += 10
    if len(text) > 120:
        score += 5
    if any(w in text for w in _UNMEASURABLE):
        score -= 45
    if "ac" not in kinds and "boundary" not in kinds:
        score -= 20
    return max(0.0, min(100.0, score))


# ---------------- ③ Wiki diff / 冲突检测 ----------------


class WikiDiff(BaseModel):
    """real 模式 LLM 输出 schema。"""

    new_rules: list[str] = []
    conflict: bool = False
    detail: str = ""


def wiki_diff(text: str, repo_id: int, module_hint: str, llm: LLMClient) -> tuple[list[str], bool, str]:
    """与系统 Wiki 模块页对比：返回 (新增规则, conflict, detail)。

    冲突判定（mock 确定性）：需求中出现 Nmin→Mmin 类变更且与 Wiki/源码 docstring 事实不符。
    """
    new_rules: list[str] = []
    conflict = False
    detail = ""

    wiki_text = ""
    with get_session() as sess:
        pages = sess.query(WikiPages).filter(WikiPages.repo_id == repo_id).all()
        wiki_text = "\n".join(p.content_md for p in pages)
        if module_hint:
            fns = sess.query(Functions).filter(Functions.repo_id == repo_id, Functions.module.contains(module_hint.split(".")[-1])).all()
            wiki_text += "\n" + "\n".join((f.docstring or "") for f in fns)

    m = re.search(r"(\d+)\s*(?:min|分钟)\s*[→>-]+\s*(\d+)\s*(?:min|分钟)", text)
    m2 = re.search(r"(?:上限|上限值|最大值|窗口)\D{0,6}(\d+)\s*[→>-]+\s*(\d+)", text)
    if m:
        old_v, new_v = m.group(1), m.group(2)
        if old_v in (wiki_text or "") and new_v not in (wiki_text or ""):
            conflict = True
            detail = (
                f"规则冲突·需求 vs 现有实现：需求要求 {old_v}min→{new_v}min，"
                f"但 Wiki/源码事实仍为 {old_v}min。确认后：更新 wiki 规则页 → 配置变更工单 → 定向重生成受影响用例"
            )
            new_rules.append(f"窗口由 {old_v}min 调整为 {new_v}min")
    elif m2:
        old_v, new_v = m2.group(1), m2.group(2)
        if old_v in (wiki_text or "") and new_v not in (wiki_text or ""):
            conflict = True
            detail = (
                f"规则冲突·需求 vs 现有实现：需求要求上限 {old_v}→{new_v}，"
                f"但 Wiki/源码事实仍为 {old_v}。确认后：更新 wiki 规则页 → 配置变更工单 → 定向重生成受影响用例"
            )
            new_rules.append(f"数量上限由 {old_v} 调整为 {new_v}")
    else:
        for r in extract_rules(text):
            if r.kind in ("boundary", "ac") and r.text[:20] not in (wiki_text or ""):
                new_rules.append(r.text)

    if llm.is_mock:
        return new_rules, conflict, detail
    out = llm.chat_json(
        [{"role": "user", "content": f"{mock_task('wikidiff')} 对比需求与 Wiki，返回 new_rules/conflict/detail\n需求：{text[:2000]}\nWiki：{wiki_text[:3000]}"}],
        schema=WikiDiff,
        mock=None,
    )
    return out.new_rules, out.conflict, out.detail


# ---------------- Parse RPC 主体 ----------------


def parse(req_code: str, title: str, body: str, source: str, repo_id: int, llm: LLMClient) -> ParseResult:
    res = ParseResult()
    res.pipeline.append("① 录入(源头)")

    text = parse_document(body, source)
    res.rules = extract_rules(text)
    res.story = next((r.text for r in res.rules if r.kind == "story"), title)
    res.testability = testability_score(text, res.rules)
    res.pipeline.append("② 解析(规则抽取)")

    module_hint = ""
    with get_session() as sess:
        fn = sess.query(Functions).filter(Functions.name == "create_order").first()
        module_hint = fn.module if fn else ""
    new_rules, conflict, detail = wiki_diff(text, repo_id, module_hint, llm)
    res.conflict = conflict
    res.conflict_detail = detail
    res.new_rules_for_wiki = new_rules
    res.pipeline.append("③ Wiki diff(冲突检测)")
    res.pipeline.append("④ 用例生成(确认后编排)")
    return res


def to_report(res: ParseResult) -> pb2.ParseReport:
    return pb2.ParseReport(
        req_code="",
        story=res.story,
        rules=[pb2.ExtractedRule(rule=r.text, change=r.change, evidence=r.evidence) for r in res.rules],
        conflict=res.conflict,
        conflict_detail=res.conflict_detail,
        testability=res.testability,
        status="",
        pipeline=res.pipeline,
    )
