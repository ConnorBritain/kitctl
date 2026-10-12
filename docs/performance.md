# Performance

Measured 2026-10-12 in the cloud build container (Linux x86_64, 4 shared vCPU, Python
3.12, kitctl 0.1.0.dev0) with `uv run python scripts/bench.py --realistic`: 60 repositories,
each with a ~2,000-directory source tree, a pruned `node_modules`, a nested `CLAUDE.md`, and
30 skills (one with vendored dependencies), plus 200 MB of synthetic Claude Code
transcripts. That is about 2,000 inventory items, a heavier load than the first real run
(357 items across 60 repos). Each step is a fresh CLI call, so times include about 40 ms
of Python startup. "Cold" means no kitctl cache. Baselines live in `performance.json`.

| Step | Time | Peak RSS | Budget |
|---|---:|---:|---|
| `kitctl --version` | 40 ms | 14 MB | < 100 ms: met |
| `inventory --json`, cold | 1.30 s | 27 MB | < 2 s: met |
| `inventory --json`, warm | 1.03 s | 27 MB | < 500 ms: **not met on this workload** |
| `usage --json`, cold (200 MB logs) | 710 ms | 22 MB | streams at disk speed, bounded memory |
| `usage --json`, warm (no new data) | 167 ms | 22 MB | < 500 ms: met |
| `report --format html`, warm | 1.22 s | 27 MB | none |

## Where warm inventory time goes

`kitctl inventory --timings` prints a per-phase breakdown. On this workload: about half is
walking repositories for nested `CLAUDE.md`/`AGENTS.md`/`.claude`/`.agents` (default depth
6), about a fifth is reading 1,800 skill folders, and the rest is startup, JSON output, and
the secret-redaction guard over every output string.

The nested walk is deliberately deep. Claude Code loads nested `CLAUDE.md` files at any
depth, so a shallower walk would hide real configuration. Measured cost of the walk alone
for 60 such repos: depth 3 or 4 about 340 ms, depth 6 about 500 ms, depth 8 about 830 ms.
Set `nested_depth` in `~/.config/kitctl/config.toml` to trade coverage for speed.

## What changed after the first real run

The first real run (60 repos on macOS) measured warm inventory at 3.1 s. On the synthetic
equivalent the old code took 6.2 s. Fixes, none of which reduce coverage:

- The nested walk now gets each directory's markers from one `scandir` instead of stat-ing
  five candidate names per directory in each adapter (the dominant cost).
- Dependency and tool-cache directories inside skills and plugins (`node_modules`, `.venv`,
  `venv`, `.tox`, and tool caches) are not walked or hashed; they are not authored
  content. `dist` and `build` are still included.
- The redaction guard checks one combined pattern before running individual rules.
- Git worktrees are skipped by default (configurable), which removes duplicate scans.

Tried and reverted: a directory-listing cache keyed by mtime cost more to load than it
saved; walking repositories on a thread pool was 10x slower because of interpreter lock
contention.

## How the other budgets are met

- `--version` imports only `kitctl` and `argparse`; subcommands import their modules lazily.
- Inventory hashes through a stat-keyed cache (`hashes.json`: path, size, `mtime_ns`,
  inode) and memoizes SKILL.md frontmatter by content hash.
- Usage keeps a byte offset per log: unchanged files are not opened, grown files are read
  from the saved offset, truncated or replaced files are re-read.

## Regression tracking

CI runs a non-blocking `bench` job (`--realistic`) on Ubuntu and compares it with
`performance.json`; steps more than 25% slower are listed in the job summary. CI runners
differ from this container, so treat it as a trend signal.
