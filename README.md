# Kit (`kitctl`)

Kit inventories the skills, MCP configuration, hooks, and plugins used by coding
agents (Claude Code, Codex and, documented but not yet verified, Cursor). The planned compiler manages named **loadouts** from a separate private
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

Loadouts (read-only; nothing is written):

```sh
uv run kitctl validate ~/kits
uv run kitctl resolve brand-studio --harness claude --warehouse ~/kits
uv run kitctl resolve brand-studio:audit --harness codex --json
uv run kitctl resolve brand-studio --harness cursor   # documented facts only; see the gaps
```

`doctor` checks a loadout against what is actually installed in the roots you
name. It never fixes anything or starts an MCP server:

```sh
uv run kitctl doctor --loadout brand-studio --root ~ --root path/to/repo
```

`use`, `off`, `plan`, `apply`, `status` and `eject` manage a project's native
config through an ownership ledger. They show the plan and ask before writing
(`--yes` skips the question), and `kitctl off` restores the original bytes (see
[apply](docs/apply.md#writing)):

```sh
uv run kitctl use brand-studio --repo path/to/repo --dry-run   # the plan only
uv run kitctl use brand-studio --repo path/to/repo             # the plan, then [y/N]
uv run kitctl off brand-studio --repo path/to/repo             # the undo
KITCTL_SANDBOX=/tmp/kit-sandbox uv run kitctl ...               # confine writes to one directory
```

`resolve` prints the effective native config a loadout would produce and every
capability gap. See [the model](docs/model.md).

`inventory` defaults to the current directory. Repeat `--root` to inspect several
repositories, or explicitly pass your home directory to include user config.
There is no recursive search through unrelated repositories. Symlinked skills and
configuration are skipped with warnings. JSON includes file paths and hashes,
never configuration contents. Exit codes: 0 complete, 1 partial due to skipped
items, 2 invalid arguments or roots. Reports may reveal private paths; keep them
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
CI runs on Linux and Windows with Python 3.11 and 3.14. A local pre-commit
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
