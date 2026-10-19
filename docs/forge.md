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
