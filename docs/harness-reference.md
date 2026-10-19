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

## Installed-version observations, 2026-10-19

The facts below are what `src/kitctl/harness.py` compiles against. Evidence
levels: **verified** means it was observed by running the installed CLI against a
sandbox `HOME`/`CLAUDE_CONFIG_DIR` and throwaway projects. **schema** means it is
present in the installed CLI's settings schema but its behavior was not
exercised. **documented** means it comes from online docs or upstream source
only. When the installed CLI and the online docs disagree, the installed CLI
wins for generators, and the disagreement is recorded here.

### Claude Code 2.1.303 (installed in the build container)

- **verified**: `claude plugin marketplace add <dir> --scope project` writes
  `.claude/settings.json` → `extraKnownMarketplaces.<name>.source`
  (`{"source": "directory", "path": …}` for a local directory).
  `claude plugin install <plugin>@<market> --scope project` writes
  `enabledPlugins["<plugin>@<market>"]: true` in the same file.
- **verified**: `claude mcp add <id> --scope project -- npx -y pkg@ver …` writes
  `.mcp.json` → `mcpServers.<id> = {type: "stdio", command, args, env}`.
  Env references use `${VAR}` and are stored unexpanded.
- **verified**: marketplace sources (`github`, `git`) accept `ref` as a
  "branch or tag" and have **no `sha` field**. Plugin entries inside a
  marketplace.json do have `sha`. A commit sha used as a marketplace ref fails:
  `git clone --branch <sha>` reports "Remote branch … not found". A marketplace
  can therefore be pinned only to a tag, and brand-builder has no tags as of
  aa4d814. Kit reports this as an `unsupported` gap. A tag is expected to work
  because `--branch` accepts tags, but that is not exercised: the local
  dumb-HTTP fixture can't serve shallow clones.
- **verified**: `claude plugin details` lists a plugin's skills, agents and
  hooks. brand-builder 0.3.1 has six skills, two agents and one PostToolUse hook.
  Plugin skills are namespaced `<plugin>:<skill>`, and Anthropic's bundled
  skills are `anthropic-skills:<skill>`.
- **verified**: `claude plugin validate [--strict] [--json] <path>` and
  `claude plugin eval [target]` exist. `eval` runs a plugin's
  `evals/**/case.yaml` suites. The first run in an untrusted plugin directory
  asks for confirmation; `--trust-plugin` answers it for CI.
- **schema**: `skillOverrides: {"<skill name>": "on" | "name-only" |
  "user-invocable-only" | "off"}`. This is a per-skill switch that the proposal
  assumed did not exist. The CLI's own guidance places it in
  `.claude/settings.local.json` for project skills or `~/.claude/settings.json`
  for user skills, and lookup normalizes `anthropic-skills:` names. Whether a
  project `.claude/settings.json` entry turns off a skill *inside a plugin* was
  not exercised: it needs a model session. Kit emits the entry with an
  `unverified` gap plus a soft rule. `disableBundledSkills` and
  `CLAUDE_CODE_DISABLE_BUNDLED_SKILLS` turn off all bundled skills at once.
- **schema**: settings precedence is user < project < local, so a project
  `enabledPlugins[id]: false` overrides a user-scope `true`. A project-scope
  `false` cannot override a `true` in `.claude/settings.local.json`. MCP servers
  from `.mcp.json` are disabled per project with `disabledMcpjsonServers` in
  `.claude/settings.local.json`; user/local servers use `disabledMcpServers` in
  the project entry of `~/.claude.json`.
- **unverified**: whether a project's `extraKnownMarketplaces` is auto-added in a
  non-interactive run. With the entry present, `claude plugin install` reported
  the plugin "not found in marketplace", which suggests it is added only after an
  interactive trust prompt.

#### Native state that inventory and doctor read (Claude Code 2.1.303, verified)

- User-scope MCP servers are stored in `.claude.json` → `mcpServers`, and
  local-scope servers in `.claude.json` → `projects["<abs path>"].mcpServers`.
  The same project entry holds `enabledMcpjsonServers` and
  `disabledMcpjsonServers`. With `CLAUDE_CONFIG_DIR` set, `.claude.json` lives
  inside that directory, not in `$HOME`, so inventory reads both
  `~/.claude.json` and `~/.claude/.claude.json` under the home root.
- `~/.claude/plugins/installed_plugins.json` (`"version": 2`) records each
  install: `scope`, `projectPath`, `version`, `installPath` and
  `gitCommitSha`. Doctor compares `gitCommitSha` with the warehouse pin.
  `known_marketplaces.json` records registered marketplace sources.
- Inventory doesn't read the trust store. It skips `[hooks.state]` and
  `[projects]` in Codex config. Claude stores no hook trust of its own beyond
  installing the plugin.

### Codex CLI (not installed in the build container)

Codex is absent here, so every Codex fact is **unverified in this environment**.
The sources below are the developers.openai.com Codex docs (read through search
snippets; the proxy refused direct fetches) and openai/codex at a309f92
(2026-10-19). The latest stable tag seen is rust-v0.163.0 (2026-10-15). The
owner's machine had 0.153.4 on 2026-10-11.

- Plugin-bundled hooks (`hooks/hooks.json` in the plugin root, or the manifest
  `hooks` entry) run only after the user reviews and trusts them. Codex sets
  `PLUGIN_ROOT` and `PLUGIN_DATA`, plus `CLAUDE_PLUGIN_ROOT` and
  `CLAUDE_PLUGIN_DATA` for compatibility.
- `[features] hooks` is stable and on by default. `codex_hooks` is a deprecated
  alias, and `plugin_hooks` is a **removed** compatibility flag, ignored if set.
  Sources disagree on which release made hooks default-on (0.124 vs 0.145).
  Before that, `codex_hooks = true` was required. Check the installed version.
- Trust state is `[hooks.state."<key>"]` with `trusted_hash`, and for plugins
  also `trusted_content_hash` over the whole plugin's contents, so any plugin
  file change likely re-prompts. Kit never writes this.
- `hooks.json`: `{"hooks": {"<Event>": [{"matcher", "hooks": [{"type":
  "command", "command", "timeout"}]}]}}`. Edits use the tool `apply_patch`, with
  matcher aliases `Write` and `Edit`. A matcher made only of letters, digits, `_`
  and `|` is an exact alternation; anything else is a regex.
- `[mcp_servers.<id>]` keys include `command`, `args`, `env`, `env_vars`,
  `cwd`, `enabled`, `required`, `enabled_tools`, `disabled_tools` (applied
  after `enabled_tools`), `startup_timeout_sec`, `tool_timeout_sec`, `url`,
  `bearer_token_env_var`, `http_headers`, `env_http_headers`.
- Marketplaces: `codex plugin marketplace add <owner/repo[@ref] | url | path>
  [--ref REF] [--sparse PATH]`. Repo marketplace files are discovered at
  `.agents/plugins/marketplace.json` (also `.claude-plugin/marketplace.json`).
  `[marketplaces.<name>]` config keys exist but include command-written state
  (`last_revision`, `last_updated`), so Kit treats registration as a
  manual-step gap.
- Plugins: `[plugins."<plugin>@<marketplace>"] enabled = true|false`, settable
  in trusted-project config.
- Skills: `[[skills.config]]` (`path` or `name`, `enabled = false`) is read only
  from the user and session layers, **not** project config (openai/codex#20210).
  A project loadout cannot disable a single Codex skill.
- Project `.codex/` layers (config, hooks, rules) load only for trusted projects.
  They override user config, and the closest file wins. Trust is stored as
  `[projects."<path>"] trust_level`; Kit never writes it.
- AGENTS.md: per directory from the root to the cwd, `AGENTS.override.md`, then
  `AGENTS.md`, then fallbacks, capped by `project_doc_max_bytes` (32 KiB).
