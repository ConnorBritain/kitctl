# Changelog

## Unreleased

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
