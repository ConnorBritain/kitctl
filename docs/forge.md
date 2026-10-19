# Forge: scout, vendor, and (M5) research-to-loadout

The agent researches and proposes. The deterministic commands quarantine,
vendor, plan and apply.

## `kitctl scout <git-url|path>… [--cache DIR] [--json]`

- Shallow-clones each source into
  `~/.cache/kitctl/quarantine/<host>/<owner>/<repo>@<sha>`. `$KITCTL_CACHE`
  or `$XDG_CACHE_HOME` override the location.
- The clone is pinned to the commit it saw. Hooks (`core.hooksPath` is set to
  the null device), LFS smudge filters, submodules, tags, system git config and
  terminal prompts are all disabled. **Nothing from the clone is executed,
  installed or built.** Scout only reads files up to 1 MB, skipping `.git`,
  `node_modules` and build output.
- Writes `<repo>@<sha>.facts.json` next to the clone and prints the facts.
  Every fact carries its evidence as `file:line` plus the trimmed line.

| Fact | How it is found |
|---|---|
| `license` | SPDX id from top-level `LICENSE`/`COPYING` text heuristics, plus `package.json` and `pyproject.toml` metadata. The value is `NOASSERTION` when none is found and `CONFLICT` when the sources disagree. |
| `last_commit` | `git log -1 --format=%cI` |
| `manifests` | `SKILL.md` (with frontmatter name/description), `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`, `marketplace.json`, `.mcp.json`, `hooks.json`, `agents/*.md` |
| `install_scripts` | npm `preinstall`/`install`/`postinstall`/`prepare`, and `curl`/`wget … | sh` or `iwr … | iex` anywhere |
| `telemetry` | analytics SDKs and hosts (PostHog, Segment, Mixpanel, Amplitude, GA, Sentry), "usage statistics", "telemetry" |
| `outbound_network` | `fetch`/`axios`/`requests`/`urlopen` calls on literal remote URLs |
| `self_fetching` | skills that fetch the latest rules at runtime, or point at `raw.githubusercontent.com` |
| `aggressive_triggers` | "MANDATORY", "always use", "execute first", "MUST be used" in skill and agent descriptions |

Facts are heuristics with evidence, not verdicts. The research step (M5) cites
them, and a human makes the final call.

## `kitctl vendor <host/owner/repo@sha> --path P [--pin SHA] [--kind K] [--name N] [--policy FILE]`

- Copies only `P` from the quarantined clone into
  `inbox/<kind-dir>/<name>/`. It writes `inbox/<kind-dir>/<name>.yaml`
  (schema 2) with `source {git, sha, path, license, retrieved}` and the
  `kit-hash-v1` content hash, and appends the repository's
  `LICENSE`/`COPYING`/`NOTICE` text to the primitive's
  `THIRD_PARTY_NOTICES.md`.
- It refuses, and writes nothing, when:
  - the license is `NOASSERTION` or `CONFLICT`;
  - the license is not allowed by policy (`--policy`, else the warehouse's
    `policy.yaml` with `licenses: {allow: [...], deny: [...]}`, else a
    permissive default);
  - the pin doesn't match the quarantined commit;
  - the path escapes the clone;
  - the source contains symlinks;
  - the destination already exists.
- Vendored primitives stay in `inbox/` until the existing triage gate moves
  them.

## `kitctl forge new <slug> --purpose "…"` (M5)

Writes `forge/<slug>/brief.yaml` in the warehouse. The brief records:
- `purpose`, `harnesses` and `target_repos`;
- `constraints`: `licenses.allow/deny`, plus `no_telemetry`,
  `no_runtime_network` and `no_install_scripts` (the first two are on by
  default);
- `required_slots` and `exclusions`;
- `evals`, each `{name, run: [argv…], cwd, expect, timeout}`.

`--slot`, `--repo`, `--exclude` and `--harness` fill in the lists.

## Research (agent work)

The `kit-forge` skill (`examples/warehouse/primitives/skills/kit-forge/`)
guides an agent from brief to `forge/<slug>/candidates.yaml`. It carries:
- the rubric: license, maintenance, mechanism, harness compatibility, overlap,
  opinion bias, network and telemetry, install scripts, trigger
  aggressiveness, and pinnability;
- the search playbook;
- the cite-everything rule;
- the brand research as a worked example.

The skill uses only `name` and `description` frontmatter, so it runs in both
Claude Code and Codex. `claude plugin validate --strict` passes on its
directory with Claude Code 2.1.303.

`candidates.yaml` is validated on every `propose`:
- `facts` must equal the scout report for that id. Facts come only from
  scout.
- `source.sha` must match the quarantined commit.
- Every stated judgment (`fills`, `overlaps`, `opinion_bias`, `verdict`,
  `config`) needs citations: `path:line` that exists in the quarantined clone,
  `fact:<key>`, or an `https://` page. Uncited, invented or out-of-range
  claims are rejected.

## `kitctl forge propose <slug>`

Deterministic. It takes the `adopt` and `vendor` verdicts that pass the
brief's constraints. A license outside the policy or unclear, telemetry not
mitigated by a configured flag, outbound network, self-fetching, install
scripts (when forbidden), and exclusions all exclude a candidate, with the
evidence line in the reason.

- Each required slot must have at most one provider; two is an error naming
  both.
- **Adopt:** writes a primitive draft into `inbox/<kind>/`.
- **Vendor:** expects the vendored primitive in `inbox/`, records `provides`
  on it, and otherwise lists the exact `kitctl vendor … --pin <sha>` command.
- Writes the draft loadout to **`inbox/loadouts/<slug>.yaml`**, so it stays
  behind the triage gate, and writes `forge/<slug>/gaps.md`: unfilled slots,
  excluded candidates with reasons, vendors still to run, and borrow-ideas
  items as build tasks.

## `kitctl forge verify <slug> [--with-plugin-eval]`

Runs the brief's evals without a shell, expanding globs itself, with a timeout
and an optional `expect` regex. `cwd` may be:
- `quarantine:<id>`;
- `inbox:<kind>/<name>`;
- a path.

Verify also runs:
- `claude plugin validate --strict` over vendored inbox skills and each inbox
  plugin with a `.claude-plugin/plugin.json`;
- `claude plugin eval` when asked, because it runs a model.

Both are skipped when `claude` isn't installed. Results go to
`forge/<slug>/verify.json`. Verify executes only the commands the brief
declares.

For brand-studio, the eval is brand-builder's own suite:

```yaml
evals:
  - name: brand-builder suite (slop=signature, considered=clean)
    run: [node, --test, tests/scan.test.mjs, tests/render.test.mjs]
    cwd: path/to/brand-builder/plugins/brand-builder
    expect: "# fail 0"
```

On 2026-10-19 this passed, 16/16, against brand-builder aa4d814 with Node 22.22.
