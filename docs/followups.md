# Local verification follow-ups

The cloud build environment cannot reach `developers.openai.com`, `learn.chatgpt.com`,
or `docs.npmjs.com`, and has no Codex CLI. Check these on your Mac and record the
result here (date, version, outcome). Each item names what to update afterwards.

## Codex documentation links (known issue 8)

- [ ] Replace every `learn.chatgpt.com` link in `docs/harness-reference.md` with its
      `developers.openai.com/codex/...` equivalent, or annotate it if it is still the
      canonical page. Pages cited: config reference, advanced config and trust,
      skills, hooks. `developers.openai.com/codex/mcp` is known to exist.
- [ ] Confirm the plugin packaging link (`developers.openai.com/plugins/build/plugins`).

## Codex facts (update `src/kitctl/sources/codex.py`, then `VERIFIED_VERSIONS`)

Run `codex --version` first.

- [ ] `CODEX_HOME` relocates config, hooks, rules, plugins, and session logs.
- [ ] `config.toml` layers: user `$CODEX_HOME/config.toml`, project `.codex/config.toml`
      (trusted projects only), managed `/etc/codex/` files. Exact trust key shape
      (`[projects."<path>"] trust_level = "trusted"`?).
- [ ] `mcp_servers.<id>` keys: `command`, `args`, `env`, `url`, `enabled_tools`,
      `disabled_tools`, `bearer_token_env_var`, headers.
- [ ] Rules location (`$CODEX_HOME/rules/*.rules`?) and format.
- [ ] Plugin install/cache location and manifest names.
- [ ] Skills: `~/.agents/skills`, repo `.agents/skills` between cwd and repo root,
      `/etc/codex/skills`; is `$CODEX_HOME/skills` still read?
- [ ] `AGENTS.override.md` precedence over `AGENTS.md` at each level.
- [ ] `codex mcp list --json` exists and its output shape (needed by `kitctl conformance`).
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

- [ ] `claude --version` and `codex --version` on your Mac (PLAN.md records 2.1.86 and
      0.153.4 from 2026-10-11; the cloud build verified Claude Code 2.1.296).
      Update `VERIFIED_VERSIONS` in each source adapter and `docs/harness-reference.md`.
