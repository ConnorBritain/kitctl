# Local verification follow-ups

The cloud build environment cannot reach `developers.openai.com`, `learn.chatgpt.com`,
or `docs.npmjs.com`, and has no Codex CLI. Check these on your Mac and record the
result here (date, version, outcome). Each item names what to update afterwards.

## Codex documentation links (known issue 8)

- [x] (2026-10-12, macOS run) Links resolve; developers.openai.com redirects to
      learn.chatgpt.com, which stays canonical. Was: Replace every `learn.chatgpt.com` link in `docs/harness-reference.md` with its
      `developers.openai.com/codex/...` equivalent, or annotate it if it is still the
      canonical page. Pages cited: config reference, advanced config and trust,
      skills, hooks. `developers.openai.com/codex/mcp` is known to exist.
- [x] Confirm the plugin packaging link (`developers.openai.com/plugins/build/plugins`).

## Still open (needs runbook v2)

- [ ] Do namespaced `function_call` `call_id`s match `McpToolCall` `item.id`s? If yes,
      count namespaced calls from older logs too (`src/kitctl/usage/codex.py`).
- [ ] Does `config.toml` record plugin enabled state as `[plugins."<id>"] enabled`?
- [ ] Windows run (paths, junctions, Codex locations on Windows).

Answered by the 2026-10-12 macOS run (Claude Code 2.1.86, Codex 0.153.4) are checked
below; details are in docs/harness-reference.md.

## Codex facts (update `src/kitctl/sources/codex.py`, then `VERIFIED_VERSIONS`)

Run `codex --version` first.

- [x] `CODEX_HOME` relocates config, hooks, rules, plugins, and session logs.
- [x] `config.toml` layers: user `$CODEX_HOME/config.toml`, project `.codex/config.toml`
      (trusted projects only), managed `/etc/codex/` files. Exact trust key shape
      (`[projects."<path>"] trust_level = "trusted"`?).
- [x] `mcp_servers.<id>` keys: `command`, `args`, `env`, `url`, `enabled_tools`,
      `disabled_tools`, `bearer_token_env_var`, headers.
- [x] Rules location (`$CODEX_HOME/rules/*.rules`?) and format.
- [x] Plugin install/cache location and manifest names.
- [x] Skills: `~/.agents/skills`, repo `.agents/skills` between cwd and repo root,
      `/etc/codex/skills`; is `$CODEX_HOME/skills` still read?
- [x] `AGENTS.override.md` precedence over `AGENTS.md` at each level.
- [x] `codex mcp list --json` exists and its output shape (needed by `kitctl conformance`).
- [ ] Session log location and format (`$CODEX_HOME/sessions/**/*.jsonl`?) for the
      usage adapter: confirm `session_meta`/`turn_context` carry `cwd`, how MCP calls are
      recorded (`function_call` names like `server__tool`, or `mcp_tool_call_begin`
      events), and whether skill use is logged at all. Update `src/kitctl/usage/codex.py`
      and bump `ADAPTER_VERSION`. `kitctl usage` reports how many lines it didn't recognize.

## npm publishing

- [ ] Is npm trusted publishing (GitHub OIDC) available for the `kitctl` package and
      your account? Minimum npm CLI version? If yes, Phase 6 wires it into the release
      workflow; if not, publishing stays a documented manual step.
- [ ] `npm deprecate kitctl@0.0.0-stage "Placeholder; use kitctl@latest"` once 0.1.0rc1 is out.

## Installed versions

- [x] `claude --version` and `codex --version` on your Mac (PLAN.md records 2.1.86 and
      0.153.4 from 2026-10-11; the cloud build verified Claude Code 2.1.296).
      Update `VERIFIED_VERSIONS` in each source adapter and `docs/harness-reference.md`.
