from __future__ import annotations

import re


class MemorySecurityError(ValueError):
    pass


_SECRET = re.compile(r"(?:api[_ -]?key|secret|password|token)\s*[:=]\s*\S+", re.IGNORECASE)
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_INJECTION = re.compile(
    r"(?:ignore (?:all |previous )?instructions|system prompt|developer message|"
    r"忽略.{0,10}(?:指令|规则)|执行.{0,10}(?:工具|命令)|输出.{0,10}(?:密钥|凭据))",
    re.IGNORECASE,
)


def sanitize_memory_content(content: str) -> str:
    value = " ".join(content.split()).strip()
    if not value:
        raise MemorySecurityError("empty memory")
    if _INJECTION.search(value):
        raise MemorySecurityError("instruction-like memory content is forbidden")
    value = _SECRET.sub("[REDACTED_SECRET]", value)
    value = _CARD.sub("[REDACTED_PAYMENT]", value)
    if "[REDACTED_SECRET]" in value or "[REDACTED_PAYMENT]" in value:
        raise MemorySecurityError("credentials and payment data cannot be stored")
    return value
