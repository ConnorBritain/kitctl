# Changelog

## Unreleased

## 0.1.1 — not yet published (tag `v0.1.1`; see docs/publishing.md)

- Output is UTF-8 on every platform. On Windows, piped output (an agent, CI, `| more`)
  used the ANSI code page, so `plan`, `apply` and `use` stopped with `'charmap' codec can't
  encode character '→'` before showing the plan, and help printed `Kit � inventory`.
- Docs: docs/publishing.md now says to verify TestPyPI from the downloaded wheel, and build the npm tarball
  around the PyPI wheel.

## 0.1.0 — 2026-10-22 (tag `v0.1.0`; see docs/publishing.md)

Kit writes: loadouts apply to a project or to your home with a plan, a confirmation and a
byte-for-byte undo.

- Worker agents: a new `agent` primitive kind (`primitives/agents/<name>.yaml` plus a
  `<name>/` folder with the author's ready `claude.md` and/or `codex.toml`) and a loadout
  `agents:` list (profiles can narrow it). Kit copies `claude.md` to
  `.claude/agents/<name>.md` and `codex.toml` to `.codex/agents/<name>.toml` as owned
  whole files, in a project or (`--user`) under `~/.claude` and `$CODEX_HOME`; it never
  converts between formats. Codex project agents carry an `unverified` gap and Cursor an
  `unsupported` one. `validate` reports a missing folder or harness file, TOML that does
  not parse, a missing Codex `name`/`description`/`developer_instructions`, and a
  `claude.md` frontmatter name that differs from the file name. Inventory lists
  `.claude/agents/*.md` and `.codex/agents/*.toml`; `init` makes `primitives/agents/`;
  forge and `vendor` refuse agents.
- Listing cost: `doctor` estimates each harness's always-on listing (instruction block,
  listed skills and agents as `name: description`, `ceil(chars / 4)` as in Gestalt) for
  `--loadout` plus the loadouts in each root's `kit.yaml`, and names what it cannot
  measure (plugin skills it cannot read, MCP servers). Skills with
  `disable-model-invocation: true` and skills the loadout disables cost nothing. A loadout
  or profile `budget: {tokens, skills}` turns an over-budget listing into a `budget`
  warning (never an error); the child layer or profile overrides its parent.

- Fixes from reviewing this release: `eject` no longer refuses an instruction block (or a
  skill or agent file) that merely mentions kitctl, which made the starter loadout
  impossible to eject; a malformed `kit.yaml` is an error message instead of a traceback;
  two whole files for one target (`acme/review` and `team/review` both landing as
  `review`) are a conflict instead of one silently overwriting the other; a folder (or
  other non-file) where Kit would write a file is a conflict at plan time instead of a
  failure halfway through apply; a `.git` folder inside a warehouse skill is not copied.
- User scope: `--user` (the same as `--repo ~`) on plan/apply/use/off/status/eject writes
  `~/.claude/settings.json`, `~/.claude/CLAUDE.md`, `~/.codex/config.toml`,
  `~/.codex/hooks.json`, `~/.codex/AGENTS.md` and user skill folders, honouring
  `CLAUDE_CONFIG_DIR` and `CODEX_HOME` within home. Intent and ledger live in `~/.kit/`.
  Claude user MCP servers are a gap with the exact `claude mcp add --scope user` command
  (owner decision: Kit does not write `~/.claude.json`); Cursor user rules are a gap.
  Codex single-skill disables become owned `[[skills.config]]` tables (TOML arrays of
  tables are now owned one table at a time, like JSON `/-` entries).
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
  no permission. Superseded by the entry above; see docs/apply.md#writing.
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
