# Changelog

## Unreleased

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

## 0.0.1 — 2026-10-11

- Initial Python CLI with version, help, and explicitly scoped read-only inventory.
- Detect skill folders and config locations; compute hashes without emitting contents.
- npm launcher bundles the same Python wheel and runs it through uvx.
- Sandbox tests, Linux/Windows CI, MIT license, and phased implementation plan.

This release does not change native agent configuration.
