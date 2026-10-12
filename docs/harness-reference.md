# Harness reference

Checked against official documentation on **2026-10-11**. These findings support
the design; no generators exist yet. Installed versions: Claude Code 2.1.86,
Codex CLI 0.153.4. Online docs may describe later features: version detection and
sandbox probes are required before claiming installed support.

## Claude Code

- [Settings scopes and precedence](https://code.claude.com/docs/en/settings):
  user ~/.claude/settings.json, shared project .claude/settings.json, local
  .claude/settings.local.json; managed and CLI sources affect precedence. List
  merging needs per-key treatment. Plugin settings include enabledPlugins and
  extraKnownMarketplaces. Hosted multi-repo behavior differs from a local session.
- [Hooks](https://code.claude.com/docs/en/hooks): matcher groups contain hooks with
  type and command. PreToolUse and SessionStart exist. PreToolUse stdin includes
  tool_name/tool_input and session context; deny through
  hookSpecificOutput.permissionDecision with hookEventName and a reason. Hook
  payload and decision fields vary by event: do not reuse one output for all.
- [MCP scopes](https://code.claude.com/docs/en/mcp): project servers use .mcp.json
  under mcpServers; both user and local server scopes live in ~/.claude.json,
  with local entries beneath the project path. Credentials may use environment
  expansion; never capture expanded values into warehouse files.
- [Permissions](https://code.claude.com/docs/en/permissions): MCP permission names
  use mcp__server__tool; deny rules override allow rules. Denying a broad server
  then allowing selected tools does not implement an allowlist. Permission denial
  is not equivalent to removing tools from server discovery.
- [Skills](https://code.claude.com/docs/en/skills): user ~/.claude/skills and
  project .claude/skills use skill directories with SKILL.md.
- [Plugin manifests](https://code.claude.com/docs/en/plugins-reference) and
  [marketplaces](https://code.claude.com/docs/en/plugin-marketplaces): manifests
  and marketplaces are separate artifacts; generate their own validated shapes
  and pin the underlying source revision rather than assuming enablement pins it.

## Codex

Links below were checked 2026-10-12 on a machine with full network access: each
resolves (HTTP 200), and `developers.openai.com/codex/*` redirects to these
`learn.chatgpt.com` pages, so they are the canonical locations (known issue 8 closed).

- [Configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference):
  MCP servers use mcp_servers.<id>, with enabled_tools and disabled_tools;
  exclusions apply after inclusion. Plugin-provided servers have plugin-scoped
  policy keys. Hook matcher tables can also be expressed inline in TOML.
- [Layers and trust](https://learn.chatgpt.com/docs/config-file/config-advanced):
  project .codex layers require trusted projects. Hooks can load from hooks.json
  or inline hooks tables beside active config layers, including user ~/.codex
  and project .codex. Relative project config paths resolve from .codex.
- [Skills](https://learn.chatgpt.com/docs/build-skills): user ~/.agents/skills;
  repository .agents/skills directories between CWD and repo root; admin/system
  locations also exist. Duplicate names do not merge. Treat legacy .codex/skills
  as inventory candidates, not a guaranteed current compile target.
- [Hooks](https://learn.chatgpt.com/docs/hooks): PreToolUse and SessionStart exist.
  Non-managed hooks require review/trust of the exact definition hash; changed
  definitions become untrusted. Tool inputs have tool_name and tool_input.
  Match shell calls as Bash and edits through apply_patch aliases; deny using
  hookSpecificOutput.permissionDecision or exit 2 with a reason. Later stdin
  writes do not repeat PreToolUse; specialized tools can bypass the common path.
  Session transcripts are explicitly not a stable interface.
- [Plugin packaging](https://developers.openai.com/plugins/build/plugins): portable
  root plugin.json/mcp.json and the compatibility .codex-plugin/plugin.json layout
  differ. OpenAI settings use extensions.com.openai in the portable format.
  Plugin hooks require separate trust. Marketplace registration is documented via
  codex plugin marketplace add; don't infer file edits from a guessed schema.

## Inventory path table (kitctl 0.1.0)

Every location comes from `src/kitctl/paths.py`. "Verified" means checked against the
code.claude.com pages cited above (fetched 2026-10-12) and Claude Code 2.1.296's CLI help
in the cloud build; Codex rows are unverified until `docs/followups.md` is worked through.

| Harness | Scope | Location | Kinds | Status |
|---|---|---|---|---|
| Claude | managed | `managed-settings.json`, `managed-mcp.json`, `CLAUDE.md` in `/Library/Application Support/ClaudeCode` (macOS), `/etc/claude-code` (Linux), `C:\Program Files\ClaudeCode` and legacy `C:\ProgramData\ClaudeCode` (Windows) | settings, hook, mcp_server, instructions | Verified (docs) |
| Claude | user | `$CLAUDE_CONFIG_DIR` (default `~/.claude`): `settings.json`, `settings.local.json`, `CLAUDE.md`, `skills/`, `agents/`, `commands/`, `rules/`, `hooks/` | settings, hook, instructions, skill, agent, command, rules | Verified |
| Claude | user | `skills/synced/<account>/<skill>/SKILL.md` (claude.ai-synced skills) | skill (`provided_by: claude.ai:synced`) | Observed in 2.1.296 |
| Claude | user/local | `.claude.json` (in `$CLAUDE_CONFIG_DIR` when set, else `~/.claude.json`): `mcpServers` = user, `projects.<path>.mcpServers` = local | mcp_server | Verified |
| Claude | plugin | `$CLAUDE_CODE_PLUGIN_CACHE_DIR` (default `~/.claude/plugins`): `installed_plugins.json` (`plugins.<id>[].installPath`), `known_marketplaces.json`, `cache/<marketplace>/<plugin>/<version>/`, `synced/` | plugin, marketplace, and each plugin's skills, agents, commands, hooks, MCP | Verified (docs); record shape read tolerantly |
| Claude | user | `skills/<dir>/.claude-plugin/plugin.json` (`<name>@skills-dir`) | plugin | Verified (docs) |
| Claude | project | `.claude/settings.json`, `.mcp.json`, `.claude/{skills,agents,commands,rules,hooks}`, `CLAUDE.md`, `.claude/CLAUDE.md`, nested `CLAUDE.md` and `.claude/skills` | settings, mcp_server, hook, skill, agent, command, rules, instructions | Verified |
| Claude | local | `.claude/settings.local.json`, `CLAUDE.local.md` | settings, instructions | Verified |
| Codex | managed | `/etc/codex/*.toml`, `/etc/codex/skills` | settings, mcp_server, skill | Documented; absent on test Mac |
| Codex | user | `$CODEX_HOME` (default `~/.codex`): `config.toml` (`mcp_servers`, `projects.<path>.trust_level`, `plugins.<id>.enabled`), `hooks.json`, `AGENTS.md`, `AGENTS.override.md`, `rules/*.rules`, `skills/` and bundled `skills/.system/`, `plugins/cache/<marketplace>/<plugin>/<version>/.codex-plugin/plugin.json`; `~/.agents/skills` | settings, mcp_server, hook, instructions, rules, plugin, skill | Verified on Codex 0.153.4 (macOS); plugin enabled tables unconfirmed |
| Codex | project | `.codex/config.toml`, `.codex/hooks.json`, `AGENTS.md` (harness `shared`), `AGENTS.override.md`, `.agents/skills`, nested `AGENTS*.md` and `.agents/skills` | settings, mcp_server, hook, instructions, skill | Documented; trusted-project loading not exercised |

Phase 4 conformance will compare against `claude mcp list` (no JSON flag in 2.1.296;
lists pending `.mcp.json` approvals) and `claude plugin list --json`, plus
`codex mcp list` once its output is confirmed.

## Session logs (usage mining)

Claude Code 2.1.296 writes one JSONL file per session under
`$CLAUDE_CONFIG_DIR/projects/<encoded-cwd>/` (subagent sidechains in nested folders).
Lines carry `type` (`user`, `assistant`, `attachment`, `system`, ...), `timestamp`, `cwd`,
`sessionId`, `version`, and `message.content[]`. kitctl reads only:

| Signal | Shape | Recorded as |
|---|---|---|
| Skill invocation | assistant `tool_use` named `Skill`, `input.skill` | skill name |
| Subagent | `tool_use` named `Agent` or `Task`, `input.subagent_type` | agent name |
| MCP call | `tool_use` named `mcp__<server>__<tool>` | server (and tool) |
| Slash command | user text containing `<command-name>/name</command-name>` | command name |

Codex session logs are documented as unstable. Observed on Codex 0.153.4: records
`{type, timestamp, payload, metadata, ordinal}` under `$CODEX_HOME/sessions/**/*.jsonl` and
`archived_sessions/`. `session_meta` and `turn_context` payloads carry `cwd`. MCP calls
complete as `event_msg` with `payload.type == "item_completed"` and an `item` of type
`McpToolCall` (`id`, `server`, `tool`, ...); kitctl counts these, deduplicated by id.
`response_item` `function_call` records can carry a `namespace`; they are tallied but not
counted until their ids are confirmed to match `McpToolCall` items. No explicit skill-use
record has been observed. Unknown record types are counted and reported, never fatal.

Native listings: `codex mcp list --json` returns an array of `{name, enabled,
disabled_reason, transport, startup_timeout_sec, tool_timeout_sec, auth_status}` with no
scope; `codex plugin list --json` lists installed plugin ids and enabled state.
`claude mcp list` health-checks (starts) servers; `claude plugin list --json` lists ids,
scope, and enabled state. `kitctl conformance` compares against these.

## Precedence used for shadowing

When one name exists at several scopes, the report names the winning scope (highest first):

| Harness | Kind | Order | Source |
|---|---|---|---|
| Claude | skill, command | managed > user > project | code.claude.com skills page |
| Claude | agent | managed > project > user | code.claude.com subagents page |
| Claude | MCP server | managed > local > project > user | code.claude.com MCP scopes |
| Codex | MCP server | managed > project > user | config layering (unverified) |

Plugin-provided items are namespaced (`plugin:name`) and never shadow. Codex skills with
the same name do not merge, so both load; settings merge key by key rather than shadow.

## Consequences for the brief

The proposed Codex hook locations are documented, but activation needs explicit
trust and installed-version validation. User MCP for Claude is not settings.json.
Claude permissions cannot guarantee a strict tool exposure allowlist. Native
plugin formats differ across harnesses; do not emit one assumed shared manifest.
Session log mining needs separate installed-version investigation before Phase 4.

Before each generator, save relevant schema revisions and minimal sandbox fixtures,
verify matcher/event behavior and env-reference syntax, and record any unsupported
feature as a capability gap. This reference is a baseline, not full conformance
certification of either installed CLI.
