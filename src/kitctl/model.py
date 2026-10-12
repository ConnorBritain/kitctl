"""Inventory entry and report model (report schema v2; see docs/report.schema.json)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA_VERSION = 2
KINDS = (
    "skill",
    "agent",
    "command",
    "mcp_server",
    "plugin",
    "marketplace",
    "hook",
    "instructions",
    "settings",
    "rules",
    "link",
)
HARNESSES = ("claude", "codex", "shared")
SCOPES = ("managed", "user", "project", "local", "plugin")
SEVERITIES = ("info", "warning", "error")


@dataclass(frozen=True)
class Entry:
    kind: str
    harness: str
    scope: str
    name: str
    path: str
    content_hash: str | None
    hash_scheme: int | None
    link_target: str | None = None
    target_in_scan: bool | None = None
    provided_by: str | None = None
    repo: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in KINDS or self.harness not in HARNESSES or self.scope not in SCOPES:
            raise ValueError(f"Invalid entry identity: {self.kind}/{self.harness}/{self.scope}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Finding:
    severity: str  # only "error" makes a command exit 1
    code: str
    path: str
    message: str

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"Invalid severity: {self.severity}")

    def to_dict(self) -> dict[str, str]:
        return asdict(self)
