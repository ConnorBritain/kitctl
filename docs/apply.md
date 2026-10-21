# Plan, apply and removal (M3)

Status 2026-10-21: writes go wherever you point them, after Kit shows the plan and
you confirm (see [Writing](#writing)). `$KITCTL_SANDBOX`, when set, confines every write
to one directory.

## Files in a project

| File | Role | Committed? |
|---|---|---|
| `kit.yaml` | Intent: `loadouts` (`name` or `name:profile`, one profile per loadout), `harnesses` (`claude`, `codex`, `cursor`; default `[claude, codex]`) (a `live: true` from the earlier opt-in is read and ignored) | yes |
| `kit.lock` | Ledger: every owned entry with its owners (loadout and profile), the parents and files Kit created, and the warehouse commit. A checksum detects hand edits. | yes |
| `.kit/` | Local recovery state: `lock`, `journal.json`, and `preimages/` (original bytes of files Kit edits). It contains its own `.gitignore` (`*`). | no |

The ledger stores generated values only. Credentials are environment
references, and preimages, which may hold anything a user wrote, never leave
`.kit/`.

## Commands

```sh
kitctl use brand-studio --yes          # add to kit.yaml, plan, apply
kitctl use brand-studio:audit --yes    # profile switch: only the delta is written
kitctl plan                            # diff for the current kit.yaml; reads only
kitctl apply --yes                     # write the plan; recovers an interrupted apply first
kitctl off brand-studio --yes          # remove only that loadout's entries
kitctl status                          # loadouts, owned entries (ok/drift/missing), slots, pending trust
kitctl eject --yes                     # keep native files, drop kit.yaml, kit.lock, .kit/ and markers
```

## Writing

`use`, `off`, `apply` and `eject` print the plan, then ask `Write N files? [y/N]` on a
terminal. `--yes` skips the question (scripts, CI); without a terminal and without
`--yes`, nothing is written. `--dry-run` shows the plan and never asks.

The undo is `kitctl off <loadout>`: it removes only that loadout's entries and restores
the original bytes from `.kit/preimages`, with or without git. Before writing, Kit warns
(and never refuses) when:

- the directory is not a git repository (`off` is then the only undo);
- tracked files have uncommitted changes (Kit's edits will mix with them);
- HEAD is on `main`, `master` or the remote's default branch (a branch lets the change be
  reviewed before it lands).

When `$KITCTL_SANDBOX` is set, every write must land inside it, so a test run or a first
try cannot touch anything else. `--live` and `live: true` from the earlier opt-in are accepted and do
nothing.

```sh
git switch -c kit/first-apply                   # optional; Kit warns on main
kitctl use gestalt-flow --dry-run               # the plan, nothing written
kitctl use gestalt-flow                         # the plan, then [y/N]
kitctl status                                   # every owned entry should read ok
kitctl off gestalt-flow                         # the undo; restores the original bytes
```

Kit writes project files only; user-scope (`~/`) targets stay gaps with remedies.

**Skills.** A skill primitive with a directory (`primitives/skills/<name>/` with a
`SKILL.md`) is copied to `.claude/skills/<name>/` and `.agents/skills/<name>/`
(`.cursor/skills/` for Cursor), one owned file per asset. A changed skill in the warehouse
updates the copy; `off` removes it. An existing file Kit did not write, a symlink, or a
non-UTF-8 asset is a conflict. External skills (`external: true`) are not copied.

**Setup steps.** After `kitctl use` writes a loadout's config, it lists the loadout's
`setup` steps and asks `Run N setup step(s)? [y/N]` (`--yes` runs them). A step whose
`when: file-exists:PATH` is missing is skipped with the reason, and `${plugin:NAME}` becomes
the installed Claude Code plugin's directory (skipped when the plugin is not installed).
Steps run in the project with no shell; their outputs belong to the tool, so `off` leaves
them. `kitctl use <loadout>` again re-runs them; `apply` only lists them.

Every write takes `--dry-run`. Without `--yes`, nothing is written.

## Generators

| Harness | Target | Entries |
|---|---|---|
| Claude | `.claude/settings.json` | `enabledPlugins`, `extraKnownMarketplaces`, `skillOverrides` (JSON pointers) |
| Claude | `.mcp.json` | `mcpServers.<id>` |
| Codex | `.codex/config.toml` | `[mcp_servers.<id>]`, `[plugins."<id>"] enabled` (via tomlkit, comments kept) |
| Codex | `.codex/hooks.json` | fallback hook entries (array elements owned by value) |
| Cursor | `.cursor/mcp.json` | `mcpServers.<id>`, env and headers as `${env:NAME}` |
| Cursor | `.cursor/hooks.json` | `version`, and `hooks.<event>` entries (array elements owned by value) |
| Cursor | `.cursor/rules/kit-NAME.mdc` | the whole generated file (format `file`): frontmatter `alwaysApply: true`, then the instructions |
| Claude, Codex | `CLAUDE.md` / `AGENTS.md` | `<!-- kit:begin NAME -->` … `<!-- kit:end NAME -->` |

Every Cursor fact is documented only, never verified (see
[harness reference](harness-reference.md#cursor-documented-only-2026-10-20)), so each
Cursor effect also comes with an `unverified` gap.

## Rules

- **Ownership is precise.** Kit removes only keys and array elements it
  inserted, plus parents and files it created, and only once they are empty
  again. When an entry wanted by two loadouts carries the same value, both own
  it, and `off` keeps it until neither does.
- **Adoption.** When a key already holds exactly the wanted value, Kit records
  it as pre-existing and never removes it.
- **Conflicts block the whole apply; nothing is written.** These are:
  - a different value at a wanted key (Kit never takes over);
  - an owned value changed since apply (drift);
  - a symlinked target, or malformed JSON or TOML;
  - a duplicate or unclosed marker;
  - a user comment inside a Kit-owned TOML table (tomlkit would drop it);
  - a value that needs an installed plugin's path (`${plugin:…}`);
  - a file changed between plan and apply;
  - another run holding `.kit/lock`.
- **Transaction.** The plan re-checks preimages under the lock. It then stages
  every file beside its target, writes the journal, and replaces files one at a
  time. If it is interrupted, the next `apply` rolls forward from the journal.
  Multiple renames are not claimed to be atomic.
- **Byte-for-byte removal.** When the last owned entry leaves a file and
  nothing else in it changed meaning, the original bytes from `.kit/preimages`
  are restored. Files and directories Kit created are deleted if empty.
  Unrelated edits survive. If the user changed the file, it stays in Kit's
  formatting instead.
- **Formatting.** JSON keeps indentation, key order, line endings and the final
  newline, but `json.dumps` expands compact arrays in files Kit edits. CRLF
  files stay CRLF.
- **Not done yet.** User-scope (`~/`) targets are not generated; brand-studio is
  entirely project-scoped.
