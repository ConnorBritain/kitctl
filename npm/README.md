# kitctl

kitctl (pronounced "kit control"; colloquially "kit") shows you every skill, MCP server,
hook, plugin, and instruction file your coding agents use, in Claude Code and Codex,
across all your repositories. It tells you where each one lives, whether you actually
use it, and what is duplicated, drifted, or broken, helps you decide what to keep, and
then installs the keepers from one private warehouse (`~/kits`) as named loadouts.

**Reading never changes anything.** `inventory`, `usage`, `report`, `triage`, `plan`,
and `adopt` write only kitctl's own files and, when you ask, the warehouse. Only `apply`,
`purge`, `eject`, `capture`, and `recover` change agent configuration, and each one shows
the exact changes first, supports `--dry-run`, and asks before writing.

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

## Install from the warehouse (apply)

```sh
kitctl desired --repo .              # what the warehouse says this repo should get
kitctl apply --global --dry-run      # core loadout at user scope: show the diff
kitctl apply --global                # ...and apply it after a prompt
kitctl apply --repo .                # this repository's kit.toml
kitctl status                        # what kitctl owns, and whether anything drifted
kitctl purge --repo .                # remove exactly what kitctl added here
kitctl eject                         # keep everything as plain config; stop managing it
```

- **Warehouse:** `primitives/<kind>/<name>/` (from `kitctl adopt`), `loadouts/<name>.toml`
  (`skills`, `agents`, `commands`, `rules`, `mcp`, `hooks`, `plugins`, `extends`).
  `loadouts/core.toml` is installed at user scope by `apply --global`.
- **`kit.toml`** in a repository: `loadouts = ["web"]`, optional `harnesses = ["claude"]`,
  `[add]`/`[remove]` lists, and `[mcp.<server>]` `enabled_tools`/`disabled_tools`.
- **What apply writes:** skill folders (copied, never linked), agent/command/rule files,
  MCP servers in `~/.claude.json`/`.mcp.json` and Codex `config.toml` (comments kept),
  hook entries with their scripts, `enabledPlugins`, and marked blocks in `AGENTS.md`.
  MCP credentials stay `${NAME}` references; Codex gets `env_vars`,
  `bearer_token_env_var`, or `env_http_headers`. What a harness can't express (a strict
  tool allowlist in Claude Code, SSE servers in Codex) fails the plan with a reason.
- **Ownership:** a ledger in kitctl's state dir records every insertion; a repository
  gets a committable `kit.lock` with hashes only. Anything kitctl didn't write, or that
  changed since, is a conflict, never overwritten (`--replace` takes over an entry and
  purge restores it). Writes are staged, journaled, and swapped in atomically;
  `kitctl recover` finishes or rolls back an interrupted apply.
- **Trust:** kitctl never marks Codex projects or hooks trusted; it tells you when Codex
  will ask.
- **Capture (optional):** `kitctl capture install` adds a SessionStart hook that copies
  user-level skills and servers you add by hand into `~/kits/inbox` for triage (skipping
  anything that contains credentials), and a PreToolUse
  guard that stops agents editing kitctl-managed files (a guardrail, not a security
  boundary). `kitctl capture remove` takes both out.
- **`kitctl marketplace build`** generates a Claude Code plugin marketplace in the
  warehouse (one plugin per loadout, plus adopted plugins).

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
| `desired [--repo PATH]` | Resolved loadouts for user scope or a repository (read-only) |
| `apply (--global \| --repo PATH) [--dry-run] [--yes] [--replace]` | Install loadouts |
| `status` | Everything kitctl owns, with drift |
| `purge (--global \| --repo PATH \| --capture \| --all)` | Remove kitctl's insertions |
| `eject` | Leave plain native config and drop all ownership |
| `recover [--rollback]` | Finish or undo an interrupted apply |
| `capture install\|remove` | kitctl's SessionStart/PreToolUse hooks |
| `marketplace build` | Claude plugin marketplace in the warehouse |
| `doctor [--probe]` | Harness versions, paths, trust, hook latency risks |
| `conformance [--health-checks]` | Compare with `claude`/`codex` list commands (runs them) |

Exit codes: 0 success (informational findings allowed), 1 completed with error findings
or conflicts, 2 invalid arguments or environment. Common flags: `--no-cache`,
`--include-worktrees`, `--no-descriptions` (names-only exports).

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
- **No writes outside its own directories** (and the warehouse for triage/adopt/
  reconcile) except through the write engine, and only to the files a reviewed plan
  names; enforced by one guarded write path and by tests that fail on any other write.
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
