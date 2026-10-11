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
