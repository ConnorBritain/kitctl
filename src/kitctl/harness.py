"""Dated harness capability facts that the resolver compiles against.

Each fact has an evidence level. "verified" means observed by running the
installed CLI in a sandbox; "schema" means present in the installed CLI's
settings schema but not exercised; "documented" means only online docs or
upstream source, with no installed CLI to check. Anything short of "verified"
compiles with an "unverified" capability gap. See docs/harness-reference.md.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Fact:
    supported: bool
    evidence: str  # verified | schema | documented
    note: str


@dataclass(frozen=True)
class Harness:
    name: str
    version: str
    observed: str
    facts: dict[str, Fact]


CLAUDE = Harness(
    name="claude",
    version="2.1.303",
    observed="2026-10-19",
    facts={
        "plugin_enable": Fact(
            True, "verified", "`claude plugin install --scope project` writes enabledPlugins"
        ),
        "plugin_disable": Fact(
            True,
            "schema",
            "enabledPlugins[id] = false; precedence user < project < local per installed CLI",
        ),
        "plugin_hooks": Fact(
            True, "verified", "plugin hooks ship with the plugin (details: Hooks)"
        ),
        "skill_disable": Fact(
            True,
            "schema",
            "skillOverrides[name] = 'off' exists in the 2.1.303 settings schema; its effect on "
            "plugin skills in project settings is not exercised",
        ),
        "marketplace_file": Fact(
            True, "verified", "extraKnownMarketplaces in .claude/settings.json (marketplace add)"
        ),
        "marketplace_sha_pin": Fact(
            False,
            "verified",
            "marketplace git `ref` is a branch or tag; a commit sha fails "
            "(`git clone --branch <sha>`: remote branch not found)",
        ),
        "mcp_project_file": Fact(
            True, "verified", "`claude mcp add --scope project` writes .mcp.json"
        ),
        "skill_dir": Fact(True, "documented", "project skills live in .claude/skills/<name>/"),
        "project_trust_required": Fact(
            False, "verified", "project settings load without Kit trust"
        ),
    },
)

CODEX = Harness(
    name="codex",
    version="0.163.0",
    observed="2026-10-19",
    facts={
        "plugin_enable": Fact(
            True,
            "verified",
            '[plugins."<plugin>@<marketplace>"] enabled = true in a trusted project config '
            "lists the plugin as installed, enabled",
        ),
        "plugin_disable": Fact(True, "documented", '[plugins."<id>"] enabled = false'),
        "plugin_hooks": Fact(
            True,
            "verified",
            "`codex features list`: hooks stable and on, plugin_hooks removed; plugin hooks still "
            "run only after the user trusts them",
        ),
        "skill_disable": Fact(
            False,
            "verified",
            "[[skills.config]] in a trusted project config is ignored; the same entry in user "
            "config removes the skill",
        ),
        "marketplace_file": Fact(
            True,
            "verified",
            "[marketplaces.<name>] source_type/source/ref in a trusted project config is read, "
            "but Codex fetches it only on `codex plugin marketplace upgrade <name>`",
        ),
        "marketplace_sha_pin": Fact(
            True, "verified", "`--ref <sha>` and ref = <sha> check out exactly that commit"
        ),
        "mcp_project_file": Fact(
            True, "verified", "[mcp_servers.<id>] in a trusted project config is listed and enabled"
        ),
        "skill_dir": Fact(True, "documented", "repository skills live in .agents/skills/<name>/"),
        "project_trust_required": Fact(
            True,
            "verified",
            "an untrusted project's .codex/config.toml is ignored (no marketplaces or MCP servers)",
        ),
    },
)

HARNESS_FACTS = {"claude": CLAUDE, "codex": CODEX}
