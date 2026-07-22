"""Redact credentials before diagnostic text or headers reach a log sink."""

from __future__ import annotations

import re
from typing import Mapping


REDACTED = "<redacted>"
SENSITIVE_HEADER_NAMES = frozenset(
    {"authorization", "x-api-key", "api-key", "proxy-authorization"}
)

_KEY_PATTERNS = (
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{8,}\b", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b", re.IGNORECASE),
    re.compile(
        r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"
    ),
    re.compile(r"(?i)((?:x-api-key|api-key)\s*[:=]\s*)[^\s,;]+"),
    re.compile(
        r"(?i)((?:OPENAI_API_KEY|ANTHROPIC_API_KEY)\s*[:=]\s*)[^\s,;]+"
    ),
)


def redact_text(value: object) -> str:
    text = str(value)
    for pattern in _KEY_PATTERNS:
        if pattern.groups:
            text = pattern.sub(lambda match: f"{match.group(1)}{REDACTED}", text)
        else:
            text = pattern.sub(REDACTED, text)
    return text


def redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {
        name: REDACTED if name.lower() in SENSITIVE_HEADER_NAMES else value
        for name, value in headers.items()
    }
