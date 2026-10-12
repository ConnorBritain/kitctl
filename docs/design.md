# kitctl design proposal

Drafted 2026-10-11; updated 2026-10-12 as built for 0.1.0 (see "As built" at the end).
This document is not permission to apply native configuration on anyone's machine;
real applies happen only when the user runs them.

## Sources of truth

The code lives in kitctl; actual skills, connectors, scripts, plugins, loadouts,
captures, and retired content live in a separate private warehouse. `kit.toml`
declares repo intent. `kit.lock` records the exact resolved warehouse commit,
schema/compiler versions, and all kitctl-owned effects. The agent proposes; a
deterministic compiler plans, previews, and commits changes.

Default warehouse location is ~/kits; the private GitHub repository can remain
agent-kits. Initial setup asks where repositories live and stores the chosen parent
directories locally; do not hardcode ~/Code as a product default.

Warehouse layout: primitives/{skills,agents,commands,rules,mcp,hooks,plugins}/<name>/,
loadouts/, inbox/, attic/, marketplace/, and triage.toml. Each adopted primitive holds
`primitive.toml` (kind, name, description, harnesses, content_hash, and a [source] table
with origin, adopted_at, original_hash) plus `files/` (copied content) or, for MCP,
`server.json` with credentials replaced by `${NAME}` references. Nothing is retired by
deleting warehouse content.

## Versioned model

kitctl's hand-edited documents (`kit.toml`, `loadouts/*.toml`, triage decisions, user
config) are TOML, read with stdlib `tomllib`; `kit.lock` is machine-written JSON with
sorted keys. All use schema_version: 1 and reject unknown fields. Names are
restricted to safe slug characters; references cannot contain traversal or escape
their warehouse root. A primitive has kind, name, description, tags, harnesses,
source provenance, and a SHA-256 content hash. Hashes cover all skill assets and
normalized metadata, with a versioned hash scheme. Scheme 2 (0.1.0) normalizes
CRLF/CR to LF in text files (no NUL in the first 8 KB), skips junk (.DS_Store,
Thumbs.db, desktop.ini, __pycache__/, *.pyc, .git/), and hashes NFC-normalized
POSIX relative paths. Changes that only touch line endings are intentionally invisible. Stable identities include kind
and name to avoid collisions across primitive types.

Loadouts have extends, skills, mcp, hooks, plugins. Resolve inheritance in a stable
order, reject cycles and missing references, deduplicate identical identities, and
reject incompatible overrides rather than silently taking the last definition.
Core is explicit and user-scoped; repo selections cannot silently override core
policy. Policy strength ultimately depends on native harness precedence/trust.

`kit.toml` selects loadouts, adds/removes primitives, and supplies per-server tool
allowlists. Prefer qualified references such as skill:release-notes when names
are ambiguous. MCP credentials are named environment references, never literals.
The compiler rejects unrepresentable policies and emits a precise capability gap.

## Ownership ledger and transaction

Each ledger operation records target path, format, insertion identity (JSON pointer,
TOML key, array element identity, or managed marker), whether ancestors existed,
original value/bytes when safe, resulting value/hash, and warehouse commit.
Whole generated files are owned by path and hash; shared files by precise entries.
Sensitive preimages never go into a committed lockfile or warehouse. A pre-existing
secret-bearing entry is not overwritten; importing credentials needs a reviewed
environment-reference migration. Reports and local recovery state are gitignored.

Planning performs reads only and produces one deterministic diff. Apply validates
schemas, verifies preimages again under a lock, stages files beside their targets,
and atomically replaces each file. A durable journal makes partial multi-file
writes recoverable; never claim that multiple renames form a single atomic action.
Record intent before writes and completion afterward. Preserve existing file modes
and line endings. Missing paths, symlinks, concurrent edits, changed ownership, or
malformed config produce actionable conflicts, never silent takeover.

Mutations support --dry-run with no filesystem effects and explicit scope. JSON
edits preserve unrelated keys; TOML edits use vendored tomlkit to preserve comments. Keep
unchanged bytes where possible and track only insertions actually made.

## Compiler adapters

Claude: user/project settings, project .mcp.json, user/local MCP in .claude.json,
skills, plugin enablement/marketplaces, and managed CLAUDE.md block.
Codex: layered config.toml, hooks.json, .agents/skills, plugin manifests/marketplaces,
and AGENTS.md block. See harness-reference.md for dated sources and caveats.

Feature detection is tied to installed harness versions and sandbox probes;
current online docs can describe features absent from local CLI versions. Never
invent schema keys or write trust approvals on the user's behalf.

Codex MCP allowlists use native enabled_tools/disabled_tools. Claude permissions
can deny named tools but are not an equivalent server exposure filter. A finite
tool catalog must be pinned before generating the complement of an allowlist;
new tools create a gap. A blanket deny with narrower allows cannot work because
denials win. If a strict allowlist is requested and cannot be enforced, fail the
plan; consider a filtering proxy only as a separately reviewed future capability.

Each hook has a shared policy and separate harness adapters that normalize tool
names/arguments and emit documented decisions. Test both fixtures and actual
installed CLI behavior. Shell parsing, wrappers, variables, stdin sessions, and
specialized tools limit interception. KIT_APPLYING=1 and matching ledger hashes
prevent capture loops but are not authorization or security controls.

## Commands and rollout

0.0.1: --version, --help, inventory --root PATH [--json]. Only metadata and
content hashes; no transcript mining, MCP parsing, or automatic root crawling.

Next: init, full inventory, triage, adopt, add/remove, apply --global/--repo, build,
doctor --all, capture/reconcile, eject, purge. All mutations show diffs in dry-run.
Real inventory runs only after confirming roots. Parse session formats with
versioned, bounded adapters and redact sensitive content; keep reports local.
Triage proposes classifications and merges but waits for each user decision.

Reconcile is incremental and fail-open for session availability; it captures
unmanaged additions to inbox and proposes the equivalent repo loadout without
silently activating policies. Removals require repo-only versus retirement
classification. Capture never forwards real tokens or private primitives to a
public registry. Measure the <300ms warm no-change path rather than assuming it.

## Purge versus eject

Purge reverses owned insertions and restores preimages, preserving unrelated edits.
It removes only unchanged owned content; drift is a conflict with a recovery plan.
Directories are removed only if kitctl created them and they are empty.

Eject first materializes the current effective configuration as independent native
files, copies any required scripts/assets out of kitctl-managed paths, then relinquishes
ownership and removes kitctl metadata, instruction markers, and capture hooks.
Functional safety policy can survive as independently owned native hooks; kitctl's
capture/management hooks cannot. Check effective behavior and dependencies, not
just config bytes. Deleting all generated hooks while preserving policy would
otherwise be contradictory.

## Acceptance before live apply

Sandbox baseline -> global + multiple repo apply -> repeat apply (no diff) ->
interrupted write recovery -> unrelated user edits -> purge restoring baseline.
Separate eject test proves effective native behavior survives with no kitctl runtime
or warehouse dependency. Include schema failure, path traversal, Windows paths,
duplicate markers, corrupted ledger, concurrent writes, and drift conflicts.
Only then review a real dry-run diff and ask for live apply authorization.

## As built (0.1.0)

Modules: `desired.py` (warehouse + kit.toml resolution), `compile.py` (items to effects
and capability gaps), `engine.py` (ledger, plan, diff, journaled apply, recovery,
status, eject), `capture.py` (reconcile, guard, capture hooks), `marketplace.py`.

- **Effects.** Six operations, each with a precise identity: `json-key` (pointer),
  `json-element` (array path plus a hash of the element), `toml-key` (table path),
  `block` (named markers in Markdown), `file`, and `tree` (copied directory). Owners
  are `user` (core loadout), a repository path, or `capture`.
- **Planning.** For each identity: desired and owned (update only if the on-disk value
  still hashes to what kitctl wrote), desired and not owned (add if absent, adopt if
  identical, conflict if different unless `--replace`, which records the preimage and is
  refused for entries holding literal credentials), owned and not desired (remove,
  restore the preimage, or release a pre-existing entry). Drifted content is always a
  conflict. Containers kitctl created are pruned when they become empty; files it
  created are deleted when they become empty; tomlkit's padding before appended tables
  is trimmed on removal so purge restores bytes exactly.
- **Ledger.** `<state dir>/ledger.json` (XDG_STATE_HOME/kitctl, ~/Library/Application
  Support/kitctl, %LOCALAPPDATA%\kitctl\State). Records hold value hashes, created
  containers/dirs/files, and preimages only when replaced. `kit.lock` (per repository)
  holds item ids, targets relative to the repository, identities, and hashes.
- **Apply.** Lock file, preimage re-check of every file, staged copies beside targets
  (mode preserved), backups of changed files in the state dir, journal, `os.replace`
  per file, then ledger. `recover` rolls forward from staged copies or back from
  backups and refuses when a file changed in between. Dry runs neither write nor
  recover. Writes to agent config go only through `fs.agent_*`, which accept exactly the
  files of the plan being applied.
- **MCP translation.** Server definitions are normalized from Claude or Codex shape.
  Claude gets `type`/`command`/`args`/`env` or `type`/`url`/`headers` with `${NAME}`
  references (expanded by Claude Code). Codex gets `env_vars` for `NAME=${NAME}`,
  `bearer_token_env_var` for `Authorization: Bearer ${NAME}`, `env_http_headers` for
  `Header: ${NAME}`, literal values otherwise; anything else is a gap. Key names were
  checked against the Codex 0.153.4 binary's config schema strings.
- **Tool filters.** Codex: native `enabled_tools`/`disabled_tools`. Claude:
  `disabled_tools` become `permissions.deny` rules `mcp__<server>__<tool>`;
  `enabled_tools` is a gap.
- **Hooks.** A hook primitive's `[hook]` table (event, matcher, script or command,
  timeout) becomes a matcher group; scripts are copied to `hooks/kitctl/<name>/`
  (`$CLAUDE_PROJECT_DIR` for Claude project hooks, absolute paths otherwise).
- **Rules.** Markdown rules become Claude `rules/*.md` files and Codex `AGENTS.md`
  blocks; Codex `.rules` files go to `$CODEX_HOME/rules/` (user scope only).
- **Plugins.** `name@marketplace` ids become `enabledPlugins` entries; warehouse plugin
  primitives are served from `<warehouse>/marketplace` (user scope only) via
  `extraKnownMarketplaces` with a `directory` source. Codex plugin enablement is a gap.
- **Not built in 0.1.0:** a `local` scope (repository config kept out of committed
  files), Codex plugin generation, managed-scope writes.
