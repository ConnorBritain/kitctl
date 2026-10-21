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
  locations also exist. Duplicate names do not merge. (Corrected 2026-10-20, see
  below: `.codex/skills` is a live root, not legacy.)
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
- **verified**: `claude plugin validate --strict <dir>` validates the
  `skills/`, `agents/` and `commands/` components under a directory without a
  manifest. Pointed at a single skill folder, it fails with "No manifest
  found". `forge verify` therefore validates `inbox/` as a whole. The kit-forge
  skill passes.
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
  `hooks` entry) run only after the user reviews and trusts them. (Which plugin
  variables apply where is corrected for 0.163.0 below.)
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

### Codex CLI 0.163.0, sandbox probes (2026-10-19)

Installed with `npm install -g @openai/codex@0.163.0` into a sandbox prefix, with a
throwaway `HOME` and `CODEX_HOME`. Projects were marked trusted only in that
throwaway config. These facts supersede the "documented" entries above.

- **verified**: `codex features list` shows `hooks` stable and on, and `plugin_hooks`
  as **removed**. No flag is needed for plugin hooks. They still run only after the
  user trusts them, but that trust prompt was not exercised.
- **verified**: an untrusted project's `.codex/config.toml` is ignored entirely. For
  example, `codex plugin marketplace list` prints "No plugin marketplaces in scope"
  and `codex mcp list` shows no servers. Once the project is trusted, both appear.
- **verified**: `codex plugin marketplace add <url> --ref <sha>` writes only
  `[marketplaces.<name>] source_type = "git", source, ref` and checks out exactly
  that commit, so **Codex can pin to a commit** (Claude Code cannot).
- **verified**: that same table written into a trusted project's config is read,
  but nothing is fetched until `codex plugin marketplace upgrade <name>`. Kit
  therefore writes the table and reports the upgrade as a one-time manual step.
- **verified**: `[plugins."brand-builder@brand-builder"] enabled = true` in a
  trusted project's config, after the marketplace upgrade, lists the plugin as
  "installed, enabled 0.3.1". No separate `codex plugin add` is needed.
- **verified**: `[[skills.config]] name = "…" enabled = false` in a trusted
  project's config is **ignored**: `codex debug prompt-input` still lists the skill.
  The same entry in user config removes it. Single-skill disables need user scope, which
  `kitctl use <loadout> --user` writes as owned `[[skills.config]]` tables.
- **verified**: `[mcp_servers.<id>]` in a trusted project's config is listed and
  enabled by `codex mcp list`.

### Codex CLI 0.163.0, corrections (2026-10-20)

Two earlier statements in this file were wrong for 0.163.0. Both are **verified**
on Codex 0.163.0 (2026-10-20):

- `.codex/skills` is a live skill root, not a legacy one. Skills load from
  `$CODEX_HOME/skills`, `~/.agents/skills`, `.agents/skills` and `.codex/skills`.
  Kit still compiles project skills to `.agents/skills/<name>/`, and inventory
  already reads `.codex/skills`.
- `${PLUGIN_ROOT}` and `${PLUGIN_DATA}` are substituted in an Agent Plugin's MCP
  stdio config, whose `cwd` must be `./`, `${PLUGIN_ROOT}` or `${PLUGIN_DATA}`.
  Plugin hook commands get `${CLAUDE_PLUGIN_ROOT}` instead. Earlier text said
  Codex sets all four variables everywhere.

## Worker agents (`agent_file`, 2026-10-21)

Kit copies an agent author's ready file per harness; it never renders one format into the
other. The facts in `src/kitctl/harness.py`:

| Harness | Fact | Target | Evidence | What it rests on |
|---|---|---|---|---|
| Claude Code | `agent_file`, `agent_user_file` | `.claude/agents/<name>.md`, `~/.claude/agents/<name>.md` | documented | [Subagents](https://code.claude.com/docs/en/sub-agents): Markdown with `name` and `description` frontmatter, project and user folders. Like `skill_dir`, not flagged as a gap. |
| Codex | `agent_user_file` | `$CODEX_HOME/agents/<name>.toml` | verified | String `name`, `description` and `developer_instructions` load in Codex 0.163; checked by a sibling project's installer (Gestalt, Cairn), not in Kit's own sandbox. Codex ignores a file missing `developer_instructions`. |
| Codex | `agent_file` | `.codex/agents/<name>.toml` | documented | Assumed by analogy with the user folder (and with `.codex/skills`); no run has shown Codex loading it, so every project agent carries an `unverified` gap. |
| Cursor | `agent_file` | – | documented (absence) | No agent file location in the sources below; an `unsupported` gap with a by-hand remedy. |

## Cursor (documented only, 2026-10-20)

No Cursor CLI is installed in the build container, so **nothing about Cursor is
verified**: `src/kitctl/harness.py` records version "unknown (no installed CLI)",
every fact is **documented**, and every effect Kit compiles for Cursor carries an
`unverified` gap. Sources, all read 2026-10-20: a clone of
[cursor/plugins](https://github.com/cursor/plugins) at `29fa938`, which holds
`schemas/plugin.schema.json`, `schemas/marketplace.schema.json` and about ninety
plugins; and the project files Trail-code 0.23.0 wrote when run in a sandbox repo
(`.cursor/mcp.json`, `.cursor/rules/trail.mdc`, `.cursor/hooks.json`), which is vendor
evidence for the project-file locations. cursor.com's docs could not be fetched that day,
so anything attributed to them below is recalled, not re-read.

| Fact | Kit's use | Evidence | What the source shows |
|---|---|---|---|
| `mcp_project_file` | `.cursor/mcp.json` → `mcpServers.<id>` = `{command, args, env}` or `{url, headers}` | documented | Trail-code wrote this project file. The clone's 89 plugin `mcp.json` files use the same server objects under `mcpServers`, and 87 add a `type` key (`http` 86, `stdio` 1). Kit writes the documented form, without `type`. |
| `mcp_env_reference` | env and header values are `${env:NAME}` | documented | Recalled from Cursor's MCP docs (interpolation in `command`, `args`, `env`, `url`, `headers`), not re-read. The clone's plugin files write `${NAME}`, for example xero's `env` and hunter's `X-API-Key`. The project-file syntax is therefore unconfirmed. Kit writes references only, never values. |
| `instruction_file` | `.cursor/rules/kit-<loadout>.mdc`, frontmatter `description` and `alwaysApply: true` | documented | Trail-code wrote `.cursor/rules/trail.mdc` with this frontmatter. Four `rules/*.mdc` files in the clone (`cursor-team-kit`, `create-plugin`, `shopify-store`) use exactly that frontmatter. |
| `hooks_project_file` | `.cursor/hooks.json` → `{"version": 1, "hooks": {"<event>": [{"command", "matcher"?, "loop_limit"?}]}}` | documented | The shape and the events `afterFileEdit`, `afterAgentResponse`, `subagentStop` and `stop` come from the clone's `advisor`, `continual-learning` and `ralph-loop` hooks. Those are plugin hooks. Trail-code wrote this project file, with `postToolUse` (and a `matcher`), `afterMCPExecution` and `sessionEnd`. No `timeout` key appears anywhere. |
| `plugin_hooks` | plugin `hook_provides` slots count | documented | Plugins ship `hooks/hooks.json`, with commands using `${CURSOR_PLUGIN_ROOT}`. |
| `plugin_enable`, `plugin_disable` | `manual-step` / `unsupported` gaps | documented (absence) | Plugins are installed from the Cursor marketplace. No project file that enables or disables one is documented, and the clone has only plugin-side manifests. |
| `marketplace_file`, `marketplace_sha_pin` | `manual-step` gap | documented (absence) | A marketplace is `.cursor-plugin/marketplace.json` inside its own repo (`schemas/marketplace.schema.json`). No project file registers one, and no commit pin is documented. |
| `skill_dir` | `.cursor/skills/<name>/` (Kit copies a warehouse skill directory there, one owned file per asset) | documented | [cursor/cookbook](https://github.com/cursor/cookbook) at `6733ef8`, `hooks/README.md`: `update-skills-on-stop.mjs` maintains `.cursor/skills/*/SKILL.md`. |
| `skill_disable` | `unsupported` gap | documented (absence) | No per-skill switch is documented. |
| `hooks_trust_required` | `trust-required` gap on every Cursor hook | documented | cursor/cookbook `6733ef8` `hooks/README.md`: "Trusted workspaces automatically load project hooks from `.cursor/hooks.json`." |
| `project_trust_required` | `unverified` gap, including a loadout that writes only the rule file | unknown | No source says whether Cursor gates the other `.cursor/` files on workspace trust. Kit claims no trust step, flags that as unverified, and the first sandbox probe has to settle it. |

Hook mapping. Kit hook primitives use Claude-style events. Kit maps `Stop` to
`stop`, and `PostToolUse` whose matcher lists only edit tools (`Edit`, `Write`,
`MultiEdit`, `NotebookEdit`, `apply_patch`) to `afterFileEdit`. It drops that
matcher, because the event already means a file edit. Every other event or
matcher (`PreToolUse`, `SessionStart`, `UserPromptSubmit`, a `PostToolUse` on
`Bash`) is an `unsupported` gap, and the managed rule file gets a soft rule. A
hook `timeout` is not written; that is an `unverified` gap.

Instructions. The rule file needs its frontmatter at the top, but a marked block
is appended to the end of a file. So Kit owns the whole generated file (format
`file` in the ledger), as design.md describes for whole generated files. `off`
deletes the file when Kit created it, and eject leaves it in place. The file has
no Kit markers, so eject has nothing to strip. An existing file at that path is
never taken over (`unmanaged` conflict), and an edited one is drift.

Inventory reads `.cursor/mcp.json` and `.cursor/hooks.json` in project roots, and
`~/.cursor/` in an explicit home root, with the same redaction as Claude and Codex.
