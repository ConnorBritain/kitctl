# kitctl

kitctl (pronounced "kit control"; colloquially "kit") shows you every skill, MCP server,
hook, plugin, and instruction file your coding agents use, in Claude Code and Codex,
across all your repositories. It tells you where each one lives, whether you actually
use it, and what is duplicated, drifted, or broken, then helps you decide what to keep
and writes you a cleanup plan.

**The 0.1.x line never modifies agent configuration.** It reads Claude Code and Codex
config, writes only its own files (config, cache, reports) and, when you ask, your
private warehouse (`~/kits`). Cleanup steps are a checklist you run yourself. Managing
config from named loadouts (apply, purge, eject) is planned for 0.2.0.

## Install

```sh
uv tool install kitctl     # recommended
pipx install kitctl        # or
pip install kitctl         # Python 3.11+, no dependencies
npx kitctl --help          # Node 20+: bundled wheel, run via uv or a local Python 3.11+
```

Use an installed `kitctl` (uv tool or pipx) for anything latency-sensitive such as
agent hooks; the npm launcher's Node → Python startup is too slow for session-start hooks,
and `kitctl doctor` flags hooks that call `npx kitctl`.

## Workflow

```sh
kitctl init                       # where your repos live (e.g. ~/Code) and ~/kits
kitctl report --format html       # everything, where it lives, usage, findings
kitctl triage                     # decide core / loadout / retire / merge / keep
kitctl plan                       # a manual cleanup checklist with exact commands
```

1. **`init`** asks which directories hold your repositories and where the warehouse is,
   and writes kitctl's own config (`~/.config/kitctl/config.toml`;
   `%APPDATA%\kitctl\config.toml` on Windows). Git worktrees are skipped by default.
2. **`report`** combines inventory, usage, and analysis into Markdown, JSON, or one
   self-contained HTML page, written to kitctl's cache dir (never into a repository).
   For each item: its locations and content hashes, calls and last use (active, stale,
   unused, or untracked), plus identical copies, drift, shadowing with the winning scope,
   MCP servers sharing a command or host, usage of things no longer installed, and
   health findings (enabled-but-missing plugins, MCP commands not on PATH, skills without
   frontmatter, invalid JSON/TOML, broken links, stale project entries).
3. **`triage`** walks each item with a proposal and its reason: merge differing copies,
   core if used across repos, loadout if repo-specific, retire if stale or unused, keep if
   there's no usage signal. Accept, override, skip, or quit and resume later. Decisions go
   to `~/kits/triage.toml`; `--list` previews without writing.
4. **`plan`** turns decisions into a checklist with exact paths and native commands
   (`claude mcp remove <name> -s <scope>`, `codex mcp remove`, `claude plugin
   disable|uninstall`), archive-then-remove steps for files, and loadout snippets. Nothing
   is applied for you.
5. **`adopt`** copies a primitive into the warehouse with its origin and hash and never
   moves the original. MCP servers are stored with credentials replaced by `${NAME}`
   references; adoption refuses anything that still looks like a secret.

## All commands

| Command | What it does |
|---|---|
| `init` | First-run setup; writes only kitctl's config |
| `inventory [--root PATH] [--json] [--timings]` | Read-only discovery |
| `usage [--since DATE]` | Skill, agent, slash-command, and MCP calls from session logs |
| `report [--format md\|json\|html] [--out DIR]` | Inventory × repo matrix, usage, findings |
| `triage [--list] [--accept-proposals]` | Decide what to keep; saves to the warehouse |
| `plan [--out DIR]` | Manual cleanup checklist from triage decisions |
| `adopt <path\|mcp:name> [--to inbox\|primitives\|attic]` | Copy into the warehouse |
| `validate` | Check warehouse loadouts, primitives, and triage.toml |
| `doctor [--probe]` | Harness versions, paths, trust, hook latency risks |
| `conformance [--health-checks]` | Compare with `claude`/`codex` list commands (runs them) |

Exit codes: 0 success (informational findings allowed), 1 completed with error findings,
2 invalid arguments or environment. Common flags: `--no-cache`, `--include-worktrees`,
`--no-descriptions` (names-only exports).

## What kitctl reads, and what it never does

- **Locations:** user and managed Claude Code and Codex config (honoring
  `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `CLAUDE_CODE_PLUGIN_CACHE_DIR`), installed plugins and
  everything they provide, claude.ai-synced and Codex system skills, and project config in
  each repository, including nested `CLAUDE.md`/`AGENTS.md` and skills. See the
  [path table](docs/harness-reference.md#inventory-path-table-kitctl-010).
- **Metadata only:** MCP transport, command basename, URL host, and environment variable
  *names*; hook event, matcher, command basename, and a hash; settings' top-level keys;
  skill names and descriptions (`--no-descriptions` drops those). Values are never
  reported, and anything secret-shaped is redacted as a final guard.
- **Usage:** only identities (skill, subagent, slash command, MCP server and tool names),
  timestamps, and working directories. Prompts, outputs, and tool arguments are never
  stored or printed. Repeat runs read only new log lines.
- **No writes outside its own directories** (and the warehouse for triage/adopt), enforced
  by a single guarded write path and by tests that fail on any other write.
- **No harness CLIs executed**, except by `conformance` and `doctor --probe`, which say so.
- **Links** (symlinks, Windows junctions) are recorded with their target and followed only
  into scanned repositories, harness config dirs, or the warehouse.
- **Content hashes** (scheme 2) ignore line endings, junk files, and vendored
  dependency folders, so the same skill hashes alike on macOS, Linux, and Windows.

Reports contain private paths; keep them out of public repositories.
[Performance](docs/performance.md) has measured timings.

## Develop

```sh
uv sync --locked
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
uv build && uv run python scripts/prepare_npm.py && npm --prefix npm test
uv run python scripts/demo_sandbox.py     # end-to-end run against a throwaway HOME
uv run python scripts/bench.py --realistic
```

Tests use throwaway HOME, CODEX_HOME, and CLAUDE_CONFIG_DIR fixtures. CI runs on Linux,
Windows, and macOS with Python 3.11 and 3.14. The version lives only in
`src/kitctl/__init__.py`; `scripts/version.py --check` fails CI on any mismatch. Releases
are tag-driven ([publishing](docs/publishing.md)). A pre-commit hook and CI check reject
common token formats (`uvx pre-commit install`).

More: [PLAN.md](PLAN.md), [design](docs/design.md),
[harness reference](docs/harness-reference.md),
[real-environment runbook](docs/runbook-real-env.md).

## Naming

kitctl ships only the `kitctl` command. AWS's Kubernetes Iteration Toolkit and Gravwell
also use a `kitctl` binary; installing those alongside kitctl can cause PATH collisions.
This project is independent of those projects and KitOps.

MIT License. The code repository and the private warehouse are deliberately separate.
