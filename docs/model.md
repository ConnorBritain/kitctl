# Warehouse model (schema version 2)

M1, 2026-10-19. This is the shape `kitctl validate` and `kitctl resolve` read. It
is for review: no generator writes native config from it yet.

## Identities and paths

A reference is `kind:name`, where kind is `skill`, `mcp`, `hook`, `plugin`,
`marketplace` or `tool`. A name is a lowercase slug with at most one namespace
segment (`anthropic/brand-guidelines`). References containing `..`, a
backslash, or an absolute path are `path-traversal` errors. So are files that
resolve outside the warehouse through a symlink.

| Kind | File | Assets |
|---|---|---|
| skill | `primitives/skills/<name>.yaml` | `primitives/skills/<name>/` (needs `SKILL.md`) |
| mcp | `primitives/mcp/<name>.yaml` | – |
| hook | `primitives/hooks/<name>.yaml` | optional `<name>/` |
| plugin | `primitives/plugins/<name>.yaml` | optional `<name>/` |
| marketplace | `primitives/marketplaces/<name>.yaml` | – |
| tool | `primitives/tools/<name>.yaml` | – |
| loadout | `loadouts/<name>.yaml` | – |

## Primitives

Common fields: `schema_version`, `kind`, `name` (must match the file),
`description`, `tags`, `harnesses`, `source`, `provides`, `conflicts`, `hash`
and `external`.

- `harnesses` is either a list (`[codex]`) or a map from harness to its native
  identity. The map keys are `id` (a plugin id such as `name@market`, or a
  marketplace name), `skill` (a native skill name such as
  `anthropic-skills:brand-guidelines`), `manifest` and `path`. When it is
  omitted, the primitive supports both harnesses.
- `source` holds provenance. It can contain `git` and `sha` (`git` requires a
  full 40-hex sha), plus `tag`, `path`, `url`, `license` and `retrieved`.
- `provides` lists the slots this primitive fills, for example
  `design.generator`. `conflicts` lists references that must never be enabled
  together with it.
- `external: true` describes something Kit does not install, such as a
  third-party plugin that a loadout disables. Kind-specific checks are skipped
  for it.
- `hash` is optional. When it is present, it must equal the computed hash.

The content hash is `kit-hash-v1:sha256:<hex>`. It covers the canonical JSON
of the metadata (without `hash`), plus each asset's relative path and bytes.
UTF-8 text assets are hashed with LF line endings, so checkouts agree across
platforms.

Kind-specific fields:

| Kind | Fields |
|---|---|
| mcp | `transport` (stdio\|http), `command`, `url`, `pin {package, version}`, `args`, `env`, `headers` |
| hook | `event`, `matcher`, `command` (list), `timeout`, `blocking`, `trust` (only `requires-user-review`), `unless_capability`, `heartbeat` |
| plugin | `marketplace` (ref), `plugin_name`, `version`, `skills`, `hook_provides`, `heartbeat` |
| skill | `plugin` (the containing plugin, for external skills) |
| tool | `check {command, expect}`, `install_hint` |

- Credentials: `env` and `headers` map a name to `{env: VAR}`. Any literal value
  is a `credential-literal` error. So is any string that looks like a token, or
  a URL with an embedded password.
- Pins: a server launched by `npx`, `uvx`, `bunx` or `pnpx` must have an exact
  `pin`. `latest` is rejected.
- `hook_provides`: slots a plugin fills only through bundled hooks. They count
  only on harnesses that have plugin hooks.
- `heartbeat: {tmpfile: NAME}` (added in M2): a log the hook appends to in the
  temp directory, one line per run: `ISO time<TAB>tool<TAB>cwd…`. `doctor`
  reads it to say "fired at …" or "never fired" for each project root. It
  never reads a trust store.
- `unless_capability`: the primitive is a fallback, active only on harnesses
  that lack the named capability (for example `plugin_hooks`).

## Loadouts

Version 1 allows `name`, `description`, `extends`, `skills`, `mcp`, `hooks` and
`plugins`. Version 2 adds:

| Field | Shape |
|---|---|
| `marketplaces` | list of marketplace refs |
| `mcp` | `{server-id: {primitive: mcp:NAME, enabled_tools?, disabled_tools?}}` |
| `slots` | `{slot: ref}`: the declared single provider |
| `disable` | list of refs of any kind |
| `requires`, `optional` | tool refs; `doctor` checks them, and Kit never installs tools |
| `setup` | `[{run: [...], when: file-exists:PATH, outputs: [...], harness: any\|claude\|codex}]` |
| `instructions` | text for the managed `CLAUDE.md` / `AGENTS.md` block |
| `profiles` | `{name: {description, skills, mcp, hooks, plugins, marketplaces, requires, optional, setup, instructions_append}}` |

A profile is addressed as `loadout:profile`. Each field present in a profile
replaces that field, and its entries must already be in the loadout: a profile
is a subset and never adds anything. Disables and slots carry over. A slot whose
provider the profile removed is reported as `unfilled`.

## Resolution rules

1. **Inheritance.** `extends` is linearized parents first, in declared order,
   with each loadout appearing once. Cycles (`a -> b -> a`) and missing
   loadouts or primitives are errors. Identical entries deduplicate. An MCP
   server id or slot defined differently by two layers is an error.
2. **Disable.** A disable of something an outer layer enables removes it
   (`status: overrides`). Disabling something the same or an inner layer
   enables is an error. A disable of something outside the Kit chain compiles
   to a native disable (`external-unverified`). When an observed set is given
   (`--observed`, and from inventory in M2), a disable of something absent from
   it is a `stale-disable` warning and emits nothing.
3. **Harness filter.** Primitives that exclude the harness are listed under
   `skipped`. A fallback primitive is skipped when the harness has its
   capability. If that capability is not verified on the installed CLI, the
   skip carries an `unverified` gap.
4. **Slots.** Each declared slot must have exactly one provider. Two providers
   are an error naming both sources. Observed unmanaged primitives count as
   providers. When the declared provider can't fill the slot on a harness, a
   fallback primitive that does is reported with `status: fallback`. Primitives
   listed in each other's `conflicts` are an error.
5. **Compile.** Each effect names its target file, format, key (a JSON pointer
   or TOML key), value, source primitive, layer, `owned: true`, and the
   evidence level of the harness fact behind it. Anything the harness can't
   express becomes a gap object: `{harness, subject, capability, severity:
   unsupported | unverified | trust-required | manual-step, reason, remedy,
   fallback}`. A gap is never a silent no-op. Unenforceable disables also add
   a soft rule to the managed instruction block. `setup` steps are listed with
   `owned: false`: their outputs belong to the project and are never purged.

The harness facts and their evidence are in `src/kitctl/harness.py` and
`docs/harness-reference.md`.
