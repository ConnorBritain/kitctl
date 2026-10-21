# Pilot runbook: brand-studio

Status, 2026-10-21: only a **dry run** has been done, and it wrote nothing.
The owner has authorized live apply as a per-repository opt-in
([apply.md](apply.md#live-apply)); without it, kitctl refuses to write anywhere
outside `$KITCTL_SANDBOX`.

The steps that need the owner are in [owner-checklist.md](owner-checklist.md).

The pilot is a private product monorepo with a branded marketing app under
`apps/marketing/`. Its own agent contract forbids deployment, credentials and
merges to main. A dry run complies with that contract.

## What `kitctl use brand-studio` would write

These are project-scoped writes: one `kit.yaml` and one `kit.lock` at the repo
root, plus the files below. The first dry run on 2026-10-19 planned 20 additions
and reported 15 gaps. After the Codex facts were verified against 0.163.0, the
same dry run plans 21 additions (the Codex marketplace entry) and reports 12
gaps.

| Harness | File | Entries |
|---|---|---|
| Claude Code | `.claude/settings.json` (new) | `extraKnownMarketplaces.brand-builder` (git); `enabledPlugins` brand-builder `true`, plus frontend-design, impeccable, taste-skill and hallmark `false`; `skillOverrides` `off` for brand-guidelines, theme-factory, ui-ux-pro-max and modern-web-guidance |
| Claude Code | `.mcp.json` (new) | `chrome-devtools` (`npx -y chrome-devtools-mcp@1.11.0 --headless --isolated --no-usage-statistics --no-performance-crux`), `leonardo` (`npx -y @adobe/leonardo-mcp@0.1.0`) |
| Claude Code | `CLAUDE.md` (existing) | appended `<!-- kit:begin brand-studio -->` block |
| Codex | `.codex/config.toml` (new) | `[mcp_servers.chrome-devtools]`, `[mcp_servers.leonardo]`, `[marketplaces.brand-builder]`, `[plugins."brand-builder@brand-builder"] enabled = true`, plus three plugin disables |
| Codex | `AGENTS.md` (existing) | appended managed block |

Not written: the brand-builder edit hook ships with the plugin on both
harnesses. The `voice-compile` setup step is listed in the plan but not run.

## Expected gaps

- **Single-skill disables.**
  - Claude: Kit writes `skillOverrides`. Whether that turns off a skill
    *inside* Anthropic's bundled skills plugin at project scope has not been
    tested.
  - Codex: a project cannot disable a single skill, because
    `[[skills.config]]` is read only from user config.
  - Both harnesses get a soft rule in the managed block.
- **Marketplace pin.**
  - Claude: a marketplace `ref` must be a branch or tag. brand-builder has no
    tags, so the marketplace would track the default branch until it is
    tagged.
  - Codex: Kit writes `[marketplaces.brand-builder]` pinned to the commit. That
    pin is verified with Codex 0.163.0. You then run
    `codex plugin marketplace upgrade brand-builder` once to fetch it.
- **Trust.** Codex loads the project's `.codex/` folder only for trusted
  projects, and runs plugin hooks only after the user reviews them. Kit never
  writes trust.
- frontend-design does not exist on Codex, so its disable is reported as not
  applicable rather than as a gap.

## Findings from the dry run (fix before any real apply)

1. **Brand files live in a subfolder.** `BRAND.md`, `DESIGN.md` and
   `.brand-builder.json` are under `apps/marketing/`. The setup step's
   `when: file-exists:BRAND.md` is checked at the repo root, so
   `voice-compile` would never run, and the instruction text points at
   root-level files. Options:
   - a pilot-specific profile or loadout whose setup step and instructions
     name `apps/marketing/BRAND.md`;
   - or a later model feature that lets a loadout set a subfolder as its base
     path.
2. **The managed blocks land in shared worker contracts.** The repo's root
   `AGENTS.md` and `CLAUDE.md` are the instructions every worker follows. A
   Kit block appended there applies repo-wide, not only to the marketing app.
   The owner should decide whether that is acceptable, or whether the block
   belongs in `apps/marketing/AGENTS.md` and `CLAUDE.md`. Claude Code and
   Codex both read nested instruction files.
3. **Vale is not installed** in the dry-run environment. It is optional; doctor
   reports it with the install hint.

## Verify (read-only)

```sh
kitctl use brand-studio --repo <pilot> --dry-run                 # the diff, gaps and setup
kitctl doctor --loadout brand-studio --root ~ --root <pilot>     # run on the owner's machine
claude plugin validate --strict <brand-builder>/plugins/brand-builder
node <brand-builder>/plugins/brand-builder/scripts/install-codex-hook.mjs --check <pilot>
```

Run `doctor` with `--root ~` on the owner's machine. It is the only check that
sees user-scope plugins and skills, such as frontend-design or Impeccable
enabled for the user. Without that root, disables are reported as
"not enabled in these roots" rather than stale. After an apply, the
heartbeat check reports "fired at …" once the hook has been trusted and a site
file has been edited.

## Apply and remove (live opt-in)

Outside a sandbox these commands need `--live` and the pilot's own opt-in
([apply.md](apply.md#live-apply)). Do not point `KITCTL_SANDBOX` at a real
repository to get past it. Commit after each write, because the next one needs
a clean tree.

```sh
kitctl use brand-studio --repo <pilot> --live --yes      # journaled, ledger in kit.lock
kitctl status --repo <pilot>                             # each owned entry should be ok
kitctl use brand-studio:audit --repo <pilot> --live --yes  # writes only the delta
kitctl off brand-studio --repo <pilot> --live --yes      # restores the original bytes
kitctl eject --repo <pilot> --live --yes                 # or: keep native files, drop Kit
```

`off` removes only brand-studio's entries. If those entries were edited
outside Kit, `off` reports drift instead of removing them. Apply on a branch,
following the repo's own rules, and never on main.
