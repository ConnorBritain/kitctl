"""Likely-secret detection shared by the repo pre-commit check and the report guard.

Callers learn which kind of secret matched, never the value.
"""

from __future__ import annotations

import re
from typing import Any

# Source strings are split so this file never matches itself.
_RULES: tuple[tuple[str, str, bool], ...] = (
    # (kind, pattern, config_only)
    ("private-key", r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE " r"KEY-----", False),
    ("github-token", r"gh[pousr]_" r"[A-Za-z0-9_]{36,}", False),
    ("github-pat", r"github_pat_" r"[A-Za-z0-9_]{50,}", False),
    ("pypi-token", r"pypi-" r"[A-Za-z0-9_-]{40,}", False),
    ("npm-token", r"npm_" r"[A-Za-z0-9]{36,}", False),
    ("aws-access-key", r"AKIA" r"[A-Z0-9]{16}", False),
    ("anthropic-key", r"sk-ant-" r"[A-Za-z0-9_-]{20,}", False),
    ("openai-key", r"sk-(?:proj|svcacct|admin|live)-" r"[A-Za-z0-9_-]{20,}", False),
    ("openai-key", r"\bsk-" r"[A-Za-z0-9]{32,}\b", False),
    ("slack-token", r"xox[abposr]-" r"[A-Za-z0-9-]{10,}", False),
    ("azure-connection", r"AccountKey=" r"[A-Za-z0-9+/]{20,}={0,2}", False),
    ("azure-connection", r"SharedAccessSignature=" r"[^;\s\"']{20,}", False),
    ("jwt", r"\beyJ" r"[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}", False),
    ("bearer-token", r"\bBearer\s+" r"[A-Za-z0-9._~+/-]{20,}=*", True),
)

CONFIG_SUFFIXES = frozenset(
    {".json", ".jsonc", ".toml", ".yaml", ".yml", ".ini", ".cfg", ".conf", ".env", ".mcp"}
)
REDACTED = "[REDACTED]"

_TEXT = tuple((kind, re.compile(pattern), config) for kind, pattern, config in _RULES)
_BYTES = tuple((kind, re.compile(pattern.encode()), config) for kind, pattern, config in _RULES)


def is_config_name(name: str) -> bool:
    lowered = name.lower()
    return any(lowered.endswith(suffix) for suffix in CONFIG_SUFFIXES) or lowered.startswith(".env")


def find(text: str, config_context: bool = True) -> list[str]:
    """Kinds of likely secrets in ``text`` (deduplicated, stable order)."""
    hits = [
        kind for kind, rule, config in _TEXT if (config_context or not config) and rule.search(text)
    ]
    return list(dict.fromkeys(hits))


def find_bytes(data: bytes, config_context: bool = True) -> list[str]:
    hits = [
        kind
        for kind, rule, config in _BYTES
        if (config_context or not config) and rule.search(data)
    ]
    return list(dict.fromkeys(hits))


def redact(text: str) -> tuple[str, list[str]]:
    kinds: list[str] = []
    for kind, rule, _ in _TEXT:
        text, count = rule.subn(REDACTED, text)
        if count:
            kinds.append(kind)
    return text, list(dict.fromkeys(kinds))


def redact_obj(value: Any) -> tuple[Any, list[str]]:
    """Return a copy of a JSON-like value with likely secrets in strings redacted."""
    found: list[str] = []

    def walk(item: Any) -> Any:
        if isinstance(item, str):
            cleaned, kinds = redact(item)
            found.extend(kinds)
            return cleaned
        if isinstance(item, dict):
            return {walk(key): walk(child) for key, child in item.items()}
        if isinstance(item, list | tuple):
            return [walk(child) for child in item]
        return item

    return walk(value), list(dict.fromkeys(found))
