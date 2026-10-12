# kitctl

kitctl (pronounced "kit control"; colloquially "kit") inventories the skills,
MCP configuration, hooks, and plugins used by coding agents. The planned compiler
manages named **loadouts** from a separate private warehouse and writes each
harness's native configuration with an ownership ledger.
The planned default warehouse location is `~/kits`; setup will ask which parent
directories contain your repositories.

**The 0.1.x line is read-only with respect to agent configuration.** It inventories
what you have and where it lives; usage, reports, triage, and cleanup plans follow.
It never modifies Claude Code or Codex configuration.

## Run

Python 3.11+:

```sh
uv tool install kitctl          # or: pipx install kitctl
kitctl init                     # asks for repo parent dirs (e.g. ~/Code) and ~/kits
kitctl inventory                # user + managed config, plugins, and every discovered repo
kitctl inventory --root ~/Code/app --json
kitctl usage                    # skill/agent/command/MCP calls mined from session logs
kitctl report --format html     # inventory x repo matrix, usage, findings (md|json|html)
kitctl doctor                   # harness versions, paths, trust, hook latency risks
kitctl validate                 # warehouse loadouts and primitives (~/kits)
```

From a checkout: `uv run kitctl ...`. Node 20+ with uv: `npx kitctl ...` (a thin launcher
around the bundled wheel; don't use it from hooks, where startup latency matters).

`init` writes only kitctl's own config (`~/.config/kitctl/config.toml`, or
`%APPDATA%\kitctl\config.toml`). `inventory` always reads user-level and managed
Claude Code and Codex locations (honoring `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, and
`CLAUDE_CODE_PLUGIN_CACHE_DIR`), installed plugins and everything they provide, and
project config in each repository: `--root` if given, else repositories discovered
under the configured parents (depth 3, skipping `node_modules`, virtualenvs, and build
dirs), else the current directory. See the
[path table](docs/harness-reference.md#inventory-path-table-kitctl-010).

Output is metadata only. For MCP servers that means transport, command basename, URL
host, and environment variable *names*; for hooks, event, matcher, command basename,
and a hash of the command; for settings, the top-level keys. Values are never
reported, and anything secret-shaped is redacted as a final guard. Links (symlinks and
Windows junctions) are entries with their resolved target; kitctl reads through a link
only into scanned repos, harness config dirs, or the warehouse. Hashes use scheme 2,
which ignores line endings and junk files. The JSON report follows
[docs/report.schema.json](docs/report.schema.json). A stat-keyed hash cache lives in
kitctl's cache dir (`--no-cache` to skip).

`usage` streams Claude Code transcripts (`$CLAUDE_CONFIG_DIR/projects/**/*.jsonl`) and,
best effort, Codex session logs (`$CODEX_HOME/sessions`), keeping only identities (skill,
subagent, slash command, MCP server and tool names), timestamps, and working directories
for repo attribution. Prompts, outputs, and tool arguments are never stored or printed.
A per-file byte offset makes repeat runs read only new lines.

`report` writes to kitctl's cache dir (`~/.cache/kitctl/reports`, `~/Library/Caches/kitctl/reports`,
or `%LOCALAPPDATA%\kitctl\Cache\reports`) and refuses `--out` inside a scanned repo unless
`--force`. It shows each item with its locations and hashes, call counts and last use
(active, stale after `stale_days`, or unused), and analysis: identical copies, drift (same
name, different content), shadowing (same name at several scopes, with the winning scope),
MCP servers sharing a command or host, and health findings such as enabled-but-missing
plugins, MCP commands not on PATH, and skills without frontmatter.

Hooks must call an installed `kitctl` (`uv tool install kitctl` or `pipx install kitctl`),
never `npx kitctl`: the Node → uv → Python chain cannot meet a session-start budget.
`kitctl doctor` and `report` flag hooks that call `npx kitctl`.
See [performance](docs/performance.md) for measured timings.

Exit codes: 0 success (informational findings allowed), 1 completed with error
findings (such as unreadable or invalid config), 2 invalid arguments or environment.
Reports reveal private paths; keep them out of public source control.

## Develop

```sh
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv build
uv run python scripts/prepare_npm.py
npm --prefix npm test
cd npm
npm pack --dry-run
```

Tests use throwaway HOME, CODEX_HOME, CLAUDE_CONFIG_DIR, and repository fixtures.
CI runs on Linux, Windows, and macOS with Python 3.11 and 3.14.
The version lives only in `src/kitctl/__init__.py`; `scripts/version.py --check`
fails CI if `npm/package.json` or the launcher disagree. A local pre-commit
configuration and CI check reject common token formats and private keys. Install
the hook with `uvx pre-commit install` if desired; this basic check is not a
comprehensive secret scanner.

See [PLAN.md](PLAN.md), [design](docs/design.md),
[harness reference](docs/harness-reference.md), and [publishing](docs/publishing.md).

## Naming

kitctl ships only the `kitctl` command. AWS's Kubernetes Iteration Toolkit and
Gravwell also use a `kitctl` binary; installing those tools alongside kitctl can
cause PATH collisions. This project is independent of those projects and KitOps.

MIT License. The code repository and private warehouse are deliberately separate.
