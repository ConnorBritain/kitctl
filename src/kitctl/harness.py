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
    version="not installed",
    observed="2026-10-19",
    facts={
        "plugin_enable": Fact(
            True, "documented", '[plugins."<plugin>@<marketplace>"] enabled in .codex/config.toml'
        ),
        "plugin_disable": Fact(True, "documented", '[plugins."<id>"] enabled = false'),
        "plugin_hooks": Fact(
            True,
            "documented",
            "plugin hooks/hooks.json run after the user trusts them; [features] hooks is stable "
            "and on by default; plugin_hooks is a removed flag (openai/codex a309f92)",
        ),
        "skill_disable": Fact(
            False,
            "documented",
            "[[skills.config]] is read only from the user and session layers, not project config",
        ),
        "marketplace_file": Fact(
            False,
            "documented",
            "registration is documented by command (codex plugin marketplace add); the "
            "[marketplaces] config keys also carry command-written state",
        ),
        "marketplace_sha_pin": Fact(
            False, "documented", "`--ref` exists; whether it accepts a commit sha is unknown"
        ),
        "mcp_project_file": Fact(True, "documented", "[mcp_servers.<id>] in .codex/config.toml"),
        "skill_dir": Fact(True, "documented", "repository skills live in .agents/skills/<name>/"),
        "project_trust_required": Fact(
            True,
            "documented",
            "project .codex/ layers (config, hooks) load only for trusted projects",
        ),
    },
)

HARNESS_FACTS = {"claude": CLAUDE, "codex": CODEX}
