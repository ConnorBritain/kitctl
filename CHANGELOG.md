# Changelog

## Unreleased

- Skill directories: a warehouse skill with a directory and a `SKILL.md` is copied into
  `.claude/skills/`, `.agents/skills/` (and `.cursor/skills/`), one owned whole file per
  asset; updates follow the warehouse and `off` removes them. Symlinks, non-UTF-8 assets
  and files Kit did not write are conflicts. Fix: an owned whole file (a skill file or a
  Cursor rule file) whose content changes is now updated instead of reported unmanaged.
- Setup steps: `kitctl use` asks to run the loadout's `setup` steps after writing
  (`--yes` runs them), skipping a step whose `when` file is missing or whose
  `${plugin:NAME}` is not installed (resolved from Claude Code's installed_plugins.json).
  Outputs stay the tool's. A failed step makes `use` exit 1.
- `kitctl init [PATH]` creates a starter warehouse (default `KIT_WAREHOUSE` or `~/kits`):
  `loadouts/starter.yaml` (an instruction block and a `tool:git` check), empty
  `primitives/<kind>/` folders, a README, and `git init` unless `--no-git`. It refuses a
  non-empty directory.
- Writes for everyone (owner decision, 2026-10-21): `use`, `off`, `apply` and `eject` write
  wherever they are pointed after showing the plan and asking `[y/N]` on a terminal
  (`--yes` skips it; with no terminal and no `--yes`, nothing is written). The undo is
  `kitctl off`. A directory that is not a git repository, uncommitted tracked changes and a
  default branch are warnings, not refusals. `$KITCTL_SANDBOX`, when set, still confines
  every write. `--live` and `live: true` from the opt-in below are accepted and ignored.
- Live apply, owner-authorized: outside `$KITCTL_SANDBOX`, `use`, `off`, `apply`
  and `eject` write only with `--live`, a committed `kit.yaml` that says
  `live: true`, no uncommitted tracked changes and a named branch other than
  main, master and `origin/HEAD`. Recovery of an interrupted apply may run on
  uncommitted files only when its journal names every one. Each unmet condition
  is named; git runs without inherited `GIT_*` variables. `kit.yaml` accepts the
  `live` key and keeps CRLF line endings. A no-op apply writes nothing and needs
  no permission. See docs/apply.md#live-apply.
- Cursor: project skills go to `.cursor/skills/<name>/` and doctor sees them there; every Cursor hook carries a `trust-required` gap (trusted workspaces load project hooks); a loadout that writes only the `.mdc` rule still gets the unverified trust gap. Source: cursor/cookbook@6733ef8.

- Cursor is a third harness (`--harness cursor`, `harnesses: [cursor]` in kit.yaml).
  It writes `.cursor/mcp.json` with `${env:NAME}` references, maps `Stop` and
  edit-only `PostToolUse` hooks to `.cursor/hooks.json` (`stop`, `afterFileEdit`),
  and generates the instruction block as a whole `.cursor/rules/kit-<loadout>.mdc`
  rule file (`alwaysApply: true`), which `off` removes and eject keeps. Plugins,
  marketplaces, disables and other hook events are gaps with remedies and
  soft rules. Every Cursor fact is documented only, from Cursor's docs and
  cursor/plugins@29fa938, so each Cursor effect also carries an `unverified` gap.
  Inventory and doctor read `.cursor/mcp.json` and `.cursor/hooks.json`. kit.yaml and
  forge briefs still default to `[claude, codex]`. `validate` now resolves for all
  three harnesses. The resolve `instructions` object gains `format`
  (`markdown-block` or `file`).
- Fix: `resolve` (and so `validate`) no longer crashes on an `external` MCP primitive
  with no `command` or `url`. It reports an `mcp_external` manual-step gap instead.
- Docs: two Codex 0.163.0 corrections. `.codex/skills` is a live skill root, and
  `${PLUGIN_ROOT}`/`${PLUGIN_DATA}` apply to Agent Plugin MCP stdio config, while hook
  commands get `${CLAUDE_PLUGIN_ROOT}`.
- Warehouse model, schema version 2 (version 1 loadouts still read): primitive kinds
  skill, mcp, hook, plugin, marketplace, tool; slots, disables, profiles, setup,
  instructions, tool requirements. See docs/model.md.
- `kitctl validate [PATH]` and read-only `kitctl resolve LOADOUT[:PROFILE] --harness
  claude|codex [--json]` with capability gaps.
- First runtime dependency: ruamel.yaml.
- Inventory (report schema 2) reads native plugin, marketplace, MCP, hook and
  managed-block state for Claude Code and Codex, redacting secret-bearing values.
- `kitctl doctor [--loadout X] [--harness H] [--root PATH]`: tool checks, MCP
  launchability, unmanaged slot conflicts, stale/ineffective disables, pin drift,
  pending trust and hook heartbeats. Reports only.
- `kitctl plan`, `apply`, `use`, `off`, `status`, `eject` with an ownership ledger
  (`kit.lock`), journaled multi-file writes and byte-for-byte removal; sandbox only
  (`KITCTL_SANDBOX`). Second runtime dependency: tomlkit. See docs/apply.md.
- `kitctl scout` quarantines candidate repos and reports license, manifests, install
  scripts, telemetry, outbound network, self-fetching skills and aggressive triggers
  with file:line evidence, never executing them. `kitctl vendor` copies one pinned
  path into inbox/ with provenance, hash and license notices. See docs/forge.md.
- `kitctl forge new|propose|verify`: briefs, cited candidate validation, a deterministic
  draft loadout in inbox/ with gaps.md, and eval runs. The `kit-forge` research skill
  ships under examples/warehouse.

## 0.0.1 — 2026-10-11

- Initial Python CLI with version, help, and explicitly scoped read-only inventory.
- Detect skill folders and config locations; compute hashes without emitting contents.
- npm launcher bundles the same Python wheel and runs it through uvx.
- Sandbox tests, Linux/Windows CI, MIT license, and phased implementation plan.

This release does not change native agent configuration.
