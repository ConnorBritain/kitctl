# Changelog

## 0.1.0rc1 — 2026-10-12

First release candidate of the 0.1.0 cleanup release: read-only inventory, usage, reports,
triage, cleanup plans, and adoption into a private warehouse for Claude Code and Codex.
No command modifies agent configuration.

Fixes from the first real-environment run (macOS, 60 repos, Claude Code 2.1.86, Codex
0.153.4):

- Read-only enforcement: all writes go through one guarded helper; `doctor` reads
  harness versions from install paths instead of running CLIs (`--probe` opts in); tests
  fail on any write outside kitctl's dirs or any subprocess.
- Codex: plugins from the `plugins/cache/<marketplace>/<plugin>/<version>` layout with
  their skills and MCP servers, bundled `skills/.system`, MCP usage from `McpToolCall`
  items, `archived_sessions`.
- Git worktrees skipped by default; usage of names missing from the inventory is listed;
  stale `.claude.json` projects flagged; hooks no longer reported as drift; disabled MCP
  servers not flagged; `--no-descriptions`.
- `kitctl conformance` compares with `claude`/`codex` list commands (it runs them;
  `--health-checks` opts into `claude mcp list`).
- `scripts/capture_fixtures.py` captures redacted config structure as test fixtures.
- `kitctl triage` (interactive, resumable; `--list`, `--accept-proposals`) saves decisions
  to `<warehouse>/triage.toml`; `kitctl plan` turns them into a manual checklist with
  native commands; `kitctl adopt` copies primitives into the warehouse with provenance and
  stores MCP servers with credentials replaced by `${NAME}` references.
- `validate` covers agents, commands, rules, inbox/attic, MCP `server.json`, and
  `triage.toml`.
- Conformance labels stale Codex plugin caches and plugin-provided Codex MCP servers as
  explained; Codex skills/agents/commands report `untracked`; usage outside the inventory
  is grouped and categorized (built-in, connector, not installed).
- Warm inventory on large repos: 6.2 s to about 1 s on a 60-repo realistic benchmark
  without reducing coverage (`inventory --timings` shows phases).

- Links are first-class inventory entries with `link_target`; kitctl reads through them
  only into allowed roots. Windows junctions are handled; broken links are findings.
- `hash_scheme` 2: CRLF/CR normalized for text, junk files skipped, NFC POSIX paths.
- New `paths` module honors `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `HOME`/`USERPROFILE`, XDG.
- Single-source version in `kitctl.__init__` with tested PEP 440 to semver mapping; the
  npm launcher locates the wheel by pattern.
- Secret detection adds Anthropic, OpenAI, Slack, Azure, JWT, and Bearer shapes and
  guards inventory output.
- Report schema 2 (`findings` replaces `warnings`); exit 1 only for error findings.
- CI adds macOS.
- `kitctl init` writes kitctl's TOML user config (repo parents, warehouse, depth).
- Inventory adapters for Claude Code (managed/user/project/local settings, `.claude.json`
  MCP scopes, skills including claude.ai-synced, agents, commands, rules, hook scripts,
  installed plugins and marketplaces with everything they provide) and Codex (best
  effort: config layers, MCP, hooks, AGENTS files, rules, plugins, skills, trust).
- Safe metadata only (names, hosts, basenames, hashes); report JSON Schema in docs.
- Repository discovery under configured parents; nested CLAUDE.md/AGENTS.md and skills.
- Findings: invalid JSON/TOML, broken links, missing plugin installs, untrusted Codex
  projects, case-only name collisions, hooks that call `npx kitctl`.
- `kitctl usage`: incremental, bounded-memory mining of Claude Code transcripts (skills,
  subagents, slash commands, MCP servers/tools) and best-effort Codex session logs, with
  repo attribution by working directory and `--since`.
- `kitctl report` (md, json, html): item x repo matrix with hashes, usage, and status;
  duplicates, drift, shadowing with precedence winners, MCP overlap, health findings.
  Written to kitctl's cache dir; the HTML is self-contained.
- `kitctl doctor` and `kitctl validate` (warehouse TOML: unknown fields, unsafe names,
  traversal, missing references, `extends` cycles).
- `scripts/bench.py` and docs/performance.md; all budgets met (warm inventory 386 ms
  over 50 repos x 30 skills, warm usage 192 ms over 200 MB of logs).

## 0.0.1 — 2026-10-11

- Initial Python CLI with version, help, and explicitly scoped read-only inventory.
- Detect skill folders and config locations; compute hashes without emitting contents.
- npm launcher bundles the same Python wheel and runs it through uvx.
- Sandbox tests, Linux/Windows CI, MIT license, and phased implementation plan.

This release does not change native agent configuration.
