# Performance

Measured 2026-10-12 in the cloud build container (Linux x86_64, 4 vCPU, Python 3.12,
kitctl 0.1.0.dev0) with `uv run python scripts/bench.py`: 50 repositories x 30 skills
(each with an asset), a 30-skill user dir, and 200 MB of synthetic Claude Code
transcripts. Each step runs in a fresh interpreter, as a real CLI call does, so times
include about 35 ms of Python startup. "Cold" means no kitctl cache; the OS file cache
is warm in both cases. Baseline numbers live in `performance.json`.

| Step | Time | Peak RSS | Budget |
|---|---:|---:|---|
| `kitctl --version` | 38 ms | 13 MB | < 100 ms: met |
| `inventory --json`, cold | 583 ms | 25 MB | < 2 s: met |
| `inventory --json`, warm | 386 ms | 25 MB | < 500 ms: met |
| `usage --json`, cold (200 MB logs) | 669 ms | 22 MB | streams at disk speed, bounded memory |
| `usage --json`, warm (no new data) | 192 ms | 22 MB | < 500 ms: met |
| `report --format html`, warm | 677 ms | 26 MB | none |

Memory stays flat as transcripts grow: logs are read line by line (lines over 1 MB are
skipped and counted), and only aggregates are kept.

## How the budgets are met

- `--version` imports only `kitctl` and `argparse`; each subcommand imports its own
  modules lazily. `python -X importtime -c "import kitctl.cli"`: 19 ms total, mostly
  `argparse` and `pathlib`. `kitctl.inventory` is 39 ms and `kitctl.report` 54 ms.
- Inventory hashes through a stat-keyed cache (`hashes.json`: path, size, `mtime_ns`,
  inode), memoizes SKILL.md frontmatter by content hash, walks with `os.scandir`, and
  resolves paths only when following a link (the only way to form a cycle).
- Usage keeps a byte offset per log (`usage-<harness>.json`): unchanged files are not
  opened, grown files are read from the saved offset, and truncated or replaced files
  are read again from the start. Lines without `"tool_use"` or `command-name` are
  skipped before JSON parsing.

Profiling found the first version's warm inventory at 617 ms: repeated stats, a
`realpath` on every directory, and re-reading SKILL.md on each run. Fixing those brought
it to 386 ms.

## Regression tracking

CI runs a non-blocking `bench` job on Ubuntu at a medium size and compares against
`performance.json`. A step more than 25% slower than the baseline is listed in the job
summary. CI runners differ from this container, so treat the comparison as a trend
signal, and refresh the baseline from CI output when the runner image changes.
