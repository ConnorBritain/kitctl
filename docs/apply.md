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

## User scope

`--user` (the same as `--repo ~`) applies a loadout to your home instead of a project:
every session on the machine sees it, so Kit warns before writing. Intent and ledger live
in `~/.kit/kit.yaml` and `~/.kit/kit.lock`; `kitctl off <loadout> --user` restores the
original bytes like it does in a project.

| Project target | User target |
|---|---|
| `.claude/settings.json`, `.claude/skills/<n>/`, `.claude/agents/<n>.md` | `~/.claude/…` (or `$CLAUDE_CONFIG_DIR/…`) |
| `CLAUDE.md` block | `~/.claude/CLAUDE.md` |
| `.codex/config.toml`, `.codex/hooks.json`, `.codex/agents/<n>.toml` | `~/.codex/…` (or `$CODEX_HOME/…`) |
| `AGENTS.md` block | `~/.codex/AGENTS.md` |
| `.agents/skills/<n>/`, `.cursor/mcp.json`, `.cursor/hooks.json` | `~/.agents/…`, `~/.cursor/…` |
| `.mcp.json` (Claude MCP) | a gap whose remedy is the `claude mcp add --scope user …` command: user servers live in `~/.claude.json`, which Claude Code rewrites all the time, so Kit does not own it |
| `.cursor/rules/kit-<loadout>.mdc`, `.cursor/skills/` | gaps: Cursor keeps user rules in its settings |
| agents on Cursor | a gap in both scopes: no Cursor agent location is verified |

Codex reads single-skill disables only from user config, so in user scope a disabled skill
becomes one `[[skills.config]]` table (`name`, `enabled = false`) Kit owns by value; your
own tables stay. `$CLAUDE_CONFIG_DIR` and `$CODEX_HOME` move the targets, but only within
home; one pointing elsewhere is a conflict. Setup steps are listed, not run: they belong to
projects. Project trust gaps do not apply.

Kit does not write through symlinks, so a `~/.claude` or `~/.codex` linked from a dotfiles
repository (stow and the like) is refused; point `CLAUDE_CONFIG_DIR` / `CODEX_HOME` at the
real folder under home instead. Kit removes only its own files from `~/.kit/`, and a
comment inside a `[[skills.config]]` table Kit owns blocks `off` until you move it.

**Skills.** A skill primitive with a directory (`primitives/skills/<name>/` with a
`SKILL.md`) is copied to `.claude/skills/<name>/` and `.agents/skills/<name>/`
(`.cursor/skills/` for Cursor), one owned file per asset. A changed skill in the warehouse
updates the copy; `off` removes it. An existing file Kit did not write, a symlink, or a
non-UTF-8 asset is a conflict. External skills (`external: true`) are not copied.

**Agents.** An agent primitive's ready files are copied whole: `claude.md` to
`.claude/agents/<name>.md` and `codex.toml` to `.codex/agents/<name>.toml`, each an owned
file with LF line endings. Updates, drift and `off` work as for skills, and an existing file
Kit did not write is a conflict. Codex project agents carry an `unverified` gap (only
`$CODEX_HOME/agents/` has been seen loading); Cursor gets an `unsupported` gap.

**Setup steps.** After `kitctl use` writes a loadout's config, it lists the loadout's
`setup` steps and asks `Run N setup step(s)? [y/N]` (`--yes` runs them). A step whose
`when: file-exists:PATH` is missing is skipped with the reason, and `${plugin:NAME}` becomes
the installed Claude Code plugin's directory (skipped when the plugin is not installed).
Steps run in the project with no shell; their outputs belong to the tool, so `off` leaves
them. `kitctl use <loadout>` again re-runs them; `apply` only lists them.

Every write takes `--dry-run`. Without a terminal and without `--yes`, nothing is written.

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
| Claude, Codex, Cursor | `.claude/skills/<n>/`, `.agents/skills/<n>/`, `.cursor/skills/<n>/` | each skill asset, a whole owned file |
| Claude, Codex | `.claude/agents/<n>.md`, `.codex/agents/<n>.toml` | the agent's ready file for that harness, a whole owned file |
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
