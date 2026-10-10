"""实体/关系抽取（LightRAG 对齐）：chunk 级 LLM 抽取 + gleaning 多轮补抽。

- 每 chunk 一次抽取调用（llm_cache 键控，同 chunk 只付一次 token）；
- gleaning：追问 LLM「是否遗漏」，把补充结果并入（默认 1 轮，config 可调）；
- Key 未配置时显式抛错（调用方决定降级策略），不在本层做假实现。
"""

from __future__ import annotations

import json
import logging
import re

log = logging.getLogger("shared.kg_extract")

EXTRACT_SYSTEM = (
    "你是测试平台的知识图谱构建专家。从给定文档中抽取测试域知识实体与关系。"
    '只输出 JSON，结构：{"entities":[{"name":"","type":"","description":""}],'
    '"relations":[{"src":"","dst":"","type":"","description":""}]}。'
    "type 从 module/function/concept/risk/flow/contract 中选；实体名用文档中的原始术语；"
    "关系要体现测试视角（可能暴露/依赖/属于/触发/破坏）。不要输出 JSON 以外的任何内容。"
)

_GLEAN_SYSTEM = (
    "你在做知识图谱抽取的第二轮补漏。对照给定文档，检查已有实体/关系清单是否遗漏了"
    "重要实体或关系。有遗漏则只输出补充项的 JSON（结构与首轮一致），没有遗漏输出 "
    '{ "entities": [], "relations": [] }。不要重复已有项，不要输出其他内容。'
)


def parse_json(raw: str) -> dict:
    """容错解析 LLM 输出：剥代码围栏、截首个 { 到末个 }（docgraph._parse_json 的独立副本）。"""
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        return {}


def _valid(obj: dict) -> tuple[list[dict], list[dict]]:
    ents = [
        e
        for e in (obj.get("entities") or [])
        if isinstance(e, dict) and (e.get("name") or "").strip()
    ]
    rels = [
        r
        for r in (obj.get("relations") or [])
        if isinstance(r, dict) and (r.get("src") or "").strip() and (r.get("dst") or "").strip()
    ]
    return ents, rels


def extract_chunk(chunk_content: str, doc_title: str, max_tokens: int = 3500) -> dict:
    """单 chunk 抽取（缓存键控）。返回 {entities: [...], relations: [...]}（字段已清洗）。"""
    from services.shared.llm_cache import chat_cached
    from services.shared.tokenizer import truncate_tokens

    body = f"文档标题：{doc_title}\n\n文档内容：\n{truncate_tokens(chunk_content, max_tokens)}"
    raw = chat_cached(body, system=EXTRACT_SYSTEM, role="extract")
    ents, rels = _valid(parse_json(raw))
    return {"entities": ents, "relations": rels}


def glean_chunk(chunk_content: str, doc_title: str, first_round: dict, max_tokens: int = 3500) -> dict:
    """单 chunk gleaning 补抽：把首轮结果给 LLM 看着追问遗漏，返回补充项（已对首轮去重）。"""
    from services.shared.llm_cache import chat_cached
    from services.shared.tokenizer import truncate_tokens

    existing = {
        "entities": [{"name": e["name"], "type": e.get("type", "")} for e in first_round.get("entities", [])],
        "relations": [
            {"src": r["src"], "dst": r["dst"], "type": r.get("type", "")} for r in first_round.get("relations", [])
        ],
    }
    body = (
        f"文档标题：{doc_title}\n\n文档内容：\n{truncate_tokens(chunk_content, max_tokens)}"
        f"\n\n已有抽取结果：\n{json.dumps(existing, ensure_ascii=False)}"
    )
    raw = chat_cached(body, system=_GLEAN_SYSTEM, role="extract")
    ents, rels = _valid(parse_json(raw))
    seen_e = {(e["name"].strip()) for e in first_round.get("entities", [])}
    seen_r = {(r["src"].strip(), r["dst"].strip(), (r.get("type") or "").strip()) for r in first_round.get("relations", [])}
    add_e = [e for e in ents if e["name"].strip() not in seen_e]
    add_r = [
        r
        for r in rels
        if (r["src"].strip(), r["dst"].strip(), (r.get("type") or "").strip()) not in seen_r
    ]
    return {"entities": add_e, "relations": add_r}


def extract_document(chunks: list[dict], doc_title: str, gleaning: int = 1, max_tokens: int = 3500) -> dict:
    """整文档抽取：逐 chunk 抽取 + gleaning，chunk 间合并（同名实体/同三元组关系在合并层去重）。

    返回 {entities, relations, chunk_count, gleaning_rounds}。
    """
    all_ents: list[dict] = []
    all_rels: list[dict] = []
    rounds_used = 0
    for ch in chunks:
        content = ch.get("content") or ""
        if not content.strip():
            continue
        got = extract_chunk(content, doc_title, max_tokens)
        used_here = 0
        for _ in range(max(0, gleaning)):
            add = glean_chunk(content, doc_title, got, max_tokens)
            if not add["entities"] and not add["relations"]:
                break
            got["entities"].extend(add["entities"])
            got["relations"].extend(add["relations"])
            used_here += 1
        rounds_used = max(rounds_used, used_here)
        all_ents.extend(got["entities"])
        all_rels.extend(got["relations"])

    # chunk 间合并：实体按名（描述去重拼接），关系按 (src,dst,type) 三元组
    ents: dict[str, dict] = {}
    for e in all_ents:
        name = e["name"].strip()
        row = ents.setdefault(name, {"name": name, "type": (e.get("type") or "concept").strip() or "concept", "description": ""})
        d = (e.get("description") or "").strip()
        if d and d not in row["description"]:
            row["description"] = (row["description"] + "\n" + d).strip()[:1500]
    rels: dict[tuple[str, str, str], dict] = {}
    for r in all_rels:
        key = (r["src"].strip(), r["dst"].strip(), (r.get("type") or "related").strip() or "related")
        row = rels.setdefault(key, {"src": key[0], "dst": key[1], "type": key[2], "description": ""})
        d = (r.get("description") or "").strip()
        if d and d not in row["description"]:
            row["description"] = (row["description"] + "\n" + d).strip()[:1000]
    return {
        "entities": list(ents.values()),
        "relations": list(rels.values()),
        "chunk_count": len(chunks),
        "gleaning_rounds": rounds_used,
    }
