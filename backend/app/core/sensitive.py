"""敏感信息过滤（OpenWiki sensitive_filter.rs 移植）：

写入侧：知识文档上传 / URL 导入时命中即拒绝（422），防止密钥随文档进库被所有人检索到。
查询侧：knowledge_search 带 hide_sensitive=1 时对 excerpt 打码。

OpenWiki 原版是 11 条正则 + SQLite contains_sensitive() 查询侧隐藏；
这里对齐为 Python 正则 + redact 展示打码，模式集一致（AWS/GitHub/Slack/PEM/JWT/OpenAI/Anthropic…）。
"""

from __future__ import annotations

import re

# (类型名, 正则)——对齐 OpenWiki sensitive_filter.rs:6-31
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("AWS Access Key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("GitHub Token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("Slack Token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("私钥块(PEM/SSH/PGP)", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY( BLOCK)?-----")),
    ("JWT", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("OpenAI Key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}")),
    ("Anthropic Key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("密码赋值", re.compile(r"(?i)\bpassword\s*[=:]\s*\S{4,}")),
    ("API Key 赋值", re.compile(r"(?i)\bapi[_-]?key\s*[=:]\s*['\"]?[A-Za-z0-9_-]{8,}")),
    ("Bearer Token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-.=+/]{20,}")),
    ("数据库连接串带凭据", re.compile(r"(?i)\b(?:mysql|postgres(?:ql)?|mongodb(?:\+srv)?)://[^\s/:@]+:[^\s/@]+@")),
]

_HIT_LABEL = "、".join(label for label, _ in PATTERNS)


def scan_sensitive(text: str) -> list[str]:
    """返回命中的敏感类型列表（去重保序）；空列表 = 干净。"""
    if not text:
        return []
    hits: list[str] = []
    for label, pat in PATTERNS:
        if pat.search(text) and label not in hits:
            hits.append(label)
    return hits


def redact_sensitive(text: str) -> str:
    """查询侧打码：命中片段替换为「[类型已隐藏]」，保留结构可读性。"""
    out = text or ""
    for label, pat in PATTERNS:
        out = pat.sub(f"[{label}已隐藏]", out)
    return out
