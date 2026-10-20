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

# No Cursor CLI is installed where these were gathered, so nothing below is "verified".
# Sources (2026-10-20): a clone of cursor/plugins at 29fa938 (schemas/plugin.schema.json,
# schemas/marketplace.schema.json, the plugins' mcp.json, hooks/hooks.json, rules/*.mdc), and
# the project files Trail-code 0.23.0 wrote in a sandbox repo (.cursor/mcp.json,
# .cursor/rules/trail.mdc, .cursor/hooks.json). cursor.com's docs could not be fetched that
# day; anything attributed to them below is recalled, not re-read.
CURSOR_SOURCE = "cursor/plugins@29fa938"
TRAIL_SOURCE = "Trail-code 0.23.0's sandbox run"

CURSOR = Harness(
    name="cursor",
    version="unknown (no installed CLI)",
    observed="2026-10-20",
    facts={
        "plugin_enable": Fact(
            False,
            "documented",
            "plugins are installed from the Cursor marketplace UI; no project file that enables "
            f"one is documented, and {CURSOR_SOURCE} ships only plugin-side manifests",
        ),
        "plugin_disable": Fact(
            False,
            "documented",
            "no project file that disables an installed Cursor plugin is documented",
        ),
        "plugin_hooks": Fact(
            True,
            "documented",
            f"plugins ship hooks/hooks.json run with ${{CURSOR_PLUGIN_ROOT}} ({CURSOR_SOURCE}: "
            "advisor, continual-learning, ralph-loop)",
        ),
        "hooks_project_file": Fact(
            True,
            "documented",
            '.cursor/hooks.json {"version": 1, "hooks": {"<event>": [{"command", "matcher"?, '
            '"loop_limit"?}]}}: the shape and the events afterFileEdit, afterAgentResponse, '
            f"subagentStop and stop are seen in {CURSOR_SOURCE} plugin hooks; {TRAIL_SOURCE} wrote "
            "this project file with postToolUse (+ matcher), afterMCPExecution and sessionEnd",
        ),
        "skill_disable": Fact(False, "documented", "no per-skill switch for Cursor is documented"),
        "marketplace_file": Fact(
            False,
            "documented",
            "marketplaces are .cursor-plugin/marketplace.json inside the marketplace repo "
            f"({CURSOR_SOURCE} schemas/marketplace.schema.json); no project file registers one",
        ),
        "marketplace_sha_pin": Fact(
            False, "documented", "no way to pin a Cursor marketplace to a commit is documented"
        ),
        "mcp_project_file": Fact(
            True,
            "documented",
            '.cursor/mcp.json {"mcpServers": {"<id>": {"command", "args", "env"} | {"url", '
            f'"headers"}}}} ({TRAIL_SOURCE} wrote this project file; {CURSOR_SOURCE} plugin '
            'mcp.json files use the same server objects, most with a "type" key)',
        ),
        "mcp_env_reference": Fact(
            True,
            "documented",
            "${env:NAME} interpolation in command, args, env, url and headers (recalled from "
            "Cursor's MCP docs, not re-read); "
            f"{CURSOR_SOURCE} plugin mcp.json files write ${{NAME}} instead, so the project-file "
            "syntax is unconfirmed",
        ),
        "instruction_file": Fact(
            True,
            "documented",
            ".cursor/rules/<name>.mdc with frontmatter description and alwaysApply: true "
            f"({TRAIL_SOURCE} wrote .cursor/rules/trail.mdc so; rules/*.mdc in {CURSOR_SOURCE})",
        ),
        "skill_dir": Fact(
            False,
            "documented",
            f"no project skill directory for Cursor is documented; {CURSOR_SOURCE} ships skills "
            "only inside plugins",
        ),
        "project_trust_required": Fact(
            False,
            "documented",
            "unknown: no source says whether Cursor gates project .cursor/ files on workspace "
            "trust; Kit claims no trust step and flags that as unverified",
        ),
    },
)

HARNESS_FACTS = {"claude": CLAUDE, "codex": CODEX, "cursor": CURSOR}
