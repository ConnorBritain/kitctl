# Plan, apply and removal (M3)

Status 2026-10-19: proven in sandbox tests only. **Live apply is not
authorized.** Every write refuses to run unless the project is inside
`$KITCTL_SANDBOX`.

## Files in a project

| File | Role | Committed? |
|---|---|---|
| `kit.yaml` | Intent: `loadouts` (`name` or `name:profile`, one profile per loadout) and `harnesses` | yes |
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

Every write takes `--dry-run`. Without `--yes`, nothing is written.

## Generators

| Harness | Target | Entries |
|---|---|---|
| Claude | `.claude/settings.json` | `enabledPlugins`, `extraKnownMarketplaces`, `skillOverrides` (JSON pointers) |
| Claude | `.mcp.json` | `mcpServers.<id>` |
| Codex | `.codex/config.toml` | `[mcp_servers.<id>]`, `[plugins."<id>"] enabled` (via tomlkit, comments kept) |
| Codex | `.codex/hooks.json` | fallback hook entries (array elements owned by value) |
| both | `CLAUDE.md` / `AGENTS.md` | `<!-- kit:begin NAME -->` … `<!-- kit:end NAME -->` |

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
- **Not done yet.** `setup` steps are listed in the plan but not run. Skill
  directory copies are not generated. User-scope (`~/`) targets are not
  generated; brand-studio is entirely project-scoped.
