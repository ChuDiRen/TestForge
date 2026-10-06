"""脱敏钩子：trace 采样/日志入库前调用，命中敏感字段一律掩码。"""

import re

PATTERNS = [
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+"), r"\1***"),
    (re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key|authorization)\s*[=:]\s*\S+"), r"\1=***"),
    (re.compile(r"\b(sk-[A-Za-z0-9]{8,})\b"), "sk-***"),
]


def sanitize_text(text: str) -> str:
    out = text or ""
    for pat, repl in PATTERNS:
        out = pat.sub(repl, out)
    return out
