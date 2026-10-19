# Kit implementation plan

Created 2026-10-11 from the supplied bootstrap brief and the user's request to
plan, build, use MIT, and reserve namespaces promptly. The brief supplies product
requirements; it is not an independent grant of permission to change live config.

## Decisions and boundaries

- Python 3.11+, uv, argparse, zero runtime dependencies in 0.0.1.
- Add tomlkit and ruamel.yaml when structured config editing begins; don't write
  generators before validating their target schemas and installed capabilities.
- Binary/package: kitctl; document pre-existing AWS and Gravwell binary collisions.
  User confirmed keeping kitctl despite the collision.
- MIT, copyright Connor England (authenticated GitHub identity).
- Tool repository: ConnorBritain/kitctl; public, as confirmed by the user.
- Warehouse: ConnorBritain/agent-kits, private, cloned at ~/kits (empty scaffold only).
- Warehouse default: ~/kits. Initial setup must ask for repository parent directories;
  there is no universal repo-root default. This user confirmed ~/Code.
- Immediate release/name reservation authorized by the user's direct request.
- Real config writes, classification decisions, and pilot repo choice remain
  explicit review gates. No real agent config changed during development.
- PyPI and TestPyPI accounts have verified email/2FA and approved GitHub trusted
  publishers. Version 0.0.1 is published and installation-tested on both registries.
- No crates.io publication requested.

### Decisions recorded 2026-10-19 (loadout proposal §8, owner answers)

- MCP version pins: Kit owns them. A package-launched server (`npx`, `uvx`, …)
  must carry `pin: {package, version}`; `latest` and unpinned launches fail
  validation. Pins are inside the content hash, so drift is a warehouse diff.
- `disable` against an outer layer: allowed at project scope where the harness's
  precedence supports it, always listed in the plan (so it is not a silent
  override of core); where the harness cannot express it, the plan carries a
  capability gap and the managed instruction block carries a soft rule.
- Forge research reports live in the private warehouse under `research/<slug>/`.
- Runtime dependency `ruamel.yaml` approved for warehouse parsing (M1) and later
  comment-preserving edits. `tomlkit` approved by the owner 2026-10-19 for M3
  (comment-preserving `.codex/config.toml` edits); no other runtime dependencies.
- Schema: documents written for the new fields use `schema_version: 2`;
  version 1 loadouts (extends, skills, mcp, hooks, plugins) are still read.

## Delivery sequence

| Phase | Deliverable and acceptance | Status |
|---|---|---|
| 0 Preflight | Tool/auth checks; missing requirements recorded | Complete |
| 1 Names | Registry/API checks plus binary collision research | Complete; requested name retained |
| 2 Bootstrap/release | Repos, useful 0.0.1, sandbox tests, Linux/Windows CI; npm and PyPI uploads verified | Complete; npm, TestPyPI, and PyPI 0.0.1 verified; all four CI jobs green |
| 3 Design/model | Review docs/design.md; schemas and validation; no generator until reviewed | M1 (2026-10-19): schema v2 model, `validate`, read-only `resolve` with capability gaps, brand-studio golden output for both harnesses. Model approved and merged 2026-10-19. |
| 4 Inventory | Explicitly authorized real roots, skill/config/MCP/plugin discovery, usage adapters, matrix, drift report | M2 (2026-10-19): inventory reads Claude/Codex plugins, marketplaces, MCP (with redacted args), hooks, installed plugin shas and managed blocks over explicit roots; `doctor` reports tools, MCP launchability, unmanaged slot conflicts, stale and ineffective disables, pin drift, pending trust and hook heartbeats. Usage adapters and the drift matrix are still pending. |
| 5 Triage | Proposed classifications/diffs; confirm choices; archive retirement; commit warehouse | Pending inventory review |
| 6 Core/apply | Sandbox policy tests, preview global diff, confirm apply, verify trust and effectiveness | M3 (2026-10-19): ledger with loadout/profile attribution, journaled project apply, `plan/apply/use/off/status/eject`, generators for brand-studio's targets; sandbox acceptance green. Writes are refused outside `$KITCTL_SANDBOX`. User-scope generators, setup execution and live apply pending. |
| 7 Capture | Guidance, write guards, incremental reconcile, loop prevention, measured <300ms warm path | Pending |
| 8 Pilot | Select low-risk repo; preview/apply, harness smoke tests, migration runbook | Pending user repo choice |
| Forge (new) | scout/vendor, then forge new/propose/verify and the kit-forge skill | M4 (2026-10-19): `scout` (quarantine, facts with file:line evidence, never executes) and `vendor` (pinned path into inbox/ with provenance, hash, notices, license policy). M5 pending. |
| 9 Removal | Purge restores baseline; eject preserves effective config with zero Kit dependencies | Sandbox proofs (M3): `off` restores the baseline byte for byte; eject leaves native files with no Kit markers, ledger or state. |

Implement and prove the ledger, conflicts, interrupted-write recovery, purge, and
eject in the sandbox before any real apply, rather than leaving those proofs until
after migration. Native hooks are guardrails with capability gaps, not a security
boundary; don't promise complete command interception.

## Preflight evidence (2026-10-11)

macOS arm64, bash. Git 2.42.0; gh 2.87.2; Python 3.14.6 (also 3.11/3.12/3.13
installed); uv 0.11.2; Node 26.8.1; npm 11.19.0; Claude Code 2.1.86; Codex CLI
0.153.4. `python` is absent, but `python3` and uv environments work. No tool
installation is required. gh user ConnorBritain; npm user connorbritain. No local
PyPI token or ~/.pypirc detected; no GitHub OIDC context locally (expected).

| Namespace | Observed result |
|---|---|
| npm kitctl, kit-ctl, kit_ctl | All registry endpoints returned HTTP 404 |
| PyPI kitctl | JSON endpoint returned HTTP 404 |
| crates.io kitctl | API returned HTTP 404 with a User-Agent |
| ConnorBritain/kitctl | Authenticated API returned HTTP 404 |
| ConnorBritain/agent-kits | Authenticated API returned HTTP 404 |
| Existing exact-name GitHub repositories | `gh search repos kitctl` returned none |
| Local PATH | No kitctl executable found |

404 means no currently visible package; registries can still reject reserved or
historically blocked names. Only a verified publish establishes ownership.
Availability is a point-in-time observation, not a reservation.

Existing tooling: [AWS](https://github.com/awslabs/eks-perf-tests) (formerly
kubernetes-iteration-toolkit), [Gravwell](https://docs.gravwell.io/v5.8.8/changelog/4.2.0.html),
and [Zwin Kit](https://github.com/Zwin-ux/kit) (adjacent agent product, ships `kit`).
Zwin's npm README advertises a kitctl crate even though the crate API returned 404;
treat that as a potential naming claim, not verified registry ownership.

## Bootstrap outcome

Public tool repo and private warehouse created and pushed. v0.0.1 tags the tested
release candidate. GitHub CI passes on Linux and Windows for Python 3.11 and 3.14.
npm kitctl@0.0.1 is published under connorbritain (the initial registry processing
placeholder also remains as 0.0.0-stage). PyPI and TestPyPI kitctl@0.0.1 are
published through GitHub trusted publishing. Both Python distributions have identical
SHA-256 hashes across TestPyPI and PyPI. Fresh installations from npm, TestPyPI,
and PyPI pass --version and read-only inventory smoke tests in temporary sandboxes.
Both package namespaces are now held. No real agent config has been modified;
the warehouse is still an empty scaffold.
