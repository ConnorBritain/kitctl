# Kit (`kitctl`)

Kit inventories the skills, MCP configuration, hooks, and plugins used by coding
agents. The planned compiler manages named **loadouts** from a separate private
warehouse and writes each harness's native configuration with an ownership ledger.
The planned default warehouse location is `~/kits`; setup will ask which parent
directories contain your repositories.

**0.0.1 is an early, read-only release.** It discovers skill folders and known
configuration files in explicitly selected roots, computes SHA-256 hashes, and
prints text or JSON. It does not yet parse connectors, count usage, compile
loadouts, reconcile changes, or modify agent configuration.

## Run

Python 3.11+:

```sh
# From a checkout (works before the PyPI release):
uv run kitctl --version
uv run kitctl inventory --root /path/to/repo --json

# After publication to PyPI:
uvx kitctl --version
uv tool install kitctl
```

Node 20+ with [uv installed](https://docs.astral.sh/uv/getting-started/installation/):

```sh
# After publication to npm:
npx kitctl --version
npx kitctl inventory --root /path/to/repo --json
```

The npm package is a thin launcher around the bundled Python wheel from the same
release. It uses `uvx` with that local wheel; it does not require a PyPI release,
run install scripts, or ship a `kit` binary. uv manages Python if needed.

`inventory` defaults to the current directory. Repeat `--root` to inspect several
repositories, or explicitly pass your home directory to include user config.
There is no recursive search through unrelated repositories. Links (symlinks and
Windows junctions) are recorded as entries with their resolved target; Kit reads
through a link only when the target is inside a scanned root, a harness config dir
(`CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `~/.agents`), or the warehouse. Broken links are
findings. JSON includes paths, hashes (`hash_scheme` 2: line endings and junk files
ignored), and findings, never configuration contents; likely secrets in output are
redacted. Exit codes: 0 success (informational findings allowed), 1 completed with
error findings, 2 invalid arguments or environment. Reports may reveal private paths; keep them
out of public source control.

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

Kit ships only the `kitctl` command. AWS's Kubernetes Iteration Toolkit and
Gravwell also use a `kitctl` binary; installing those tools alongside Kit can
cause PATH collisions. This project is independent of those projects and KitOps.

MIT License. The code repository and private warehouse are deliberately separate.
