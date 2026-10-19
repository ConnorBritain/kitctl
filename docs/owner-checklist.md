# Owner checklist: brand-studio

These steps need the owner's machine, the owner's accounts or the owner's
decision. Kit can't do them from a cloud session. Each item says what it closes.
See [pilot-brand-studio.md](pilot-brand-studio.md) for the full runbook.

Setup once, on your machine:

```sh
uv tool install git+https://github.com/ConnorBritain/kitctl
git clone https://github.com/ConnorBritain/agent-kits ~/kits   # the default warehouse path
# elsewhere? export KIT_WAREHOUSE=<path> or pass --warehouse
```

## 1. Tag brand-builder (closes the Claude marketplace-pin gap)

- [ ] Create the tag:
  ```sh
  cd <brand-builder> && git fetch origin
  git tag -a v0.3.1 aa4d814 -m "brand-builder 0.3.1"
  git push origin v0.3.1
  ```
  Or use GitHub → Releases → *Draft a new release*, with tag `v0.3.1` targeting commit `aa4d814`.
- [ ] Tell Kit's session the tag exists. It then adds `tag: v0.3.1` to the
  marketplace primitive, and the gap disappears from `kitctl resolve`.

## 2. Check Claude single-skill disables (4 unverified gaps)

This tells us whether `skillOverrides: off` in project settings turns off a skill
that ships inside a plugin.

- [ ] Make a throwaway repo inside a sandbox folder. Never point the sandbox at a real repo.
  ```sh
  export KITCTL_SANDBOX=$(mktemp -d)
  git init "$KITCTL_SANDBOX/probe" && cd "$KITCTL_SANDBOX/probe"
  kitctl use brand-studio --repo . --yes
  ```
- [ ] Run `claude` in that folder, then `/skills`. Note whether each skill is
  listed as off:
  - [ ] `anthropic-skills:brand-guidelines`
  - [ ] `anthropic-skills:theme-factory`
  - [ ] `ui-ux-pro-max`
  - [ ] `modern-web-guidance`
- [ ] Report the result (off, still on, or not installed). If they are off,
  the four gaps become verified and close. If not, they stay as soft rules in
  the managed block.
- [ ] Clean up: `kitctl off brand-studio --repo . --yes`, then delete the folder.

## 3. Confirm native ids (agent-kits)

- [ ] Run `kitctl doctor --loadout brand-studio --root ~`. Check the plugin
  ids it reports for impeccable, taste-skill and hallmark. If they differ from
  `primitives/plugins/*.yaml` in agent-kits, send them over and Kit will fix the
  warehouse on a branch.

## 4. Decisions before any real apply

- [ ] **Brand files in a subfolder.** Add a pilot profile whose setup step and
  instructions point at `apps/marketing/BRAND.md`? (yes / no / wait for a
  base-path feature)
- [ ] **Managed block placement.** Put it in the root `AGENTS.md`/`CLAUDE.md`
  (applies repo-wide), or in `apps/marketing/AGENTS.md`/`CLAUDE.md`?
- [ ] **Authorize a live apply.** kitctl refuses to write outside
  `$KITCTL_SANDBOX` until a reviewed change adds an explicit opt-in. Say whether
  you want that change made.

## 5. After a real apply (only you can do these; Kit never writes trust)

- [ ] Codex: trust the project when Codex prompts. Until then the project's
  `.codex/config.toml` is ignored.
- [ ] Codex: `codex plugin marketplace upgrade brand-builder`, run once.
- [ ] Codex: review and trust the brand-builder plugin hooks.
- [ ] Claude: approve the project's MCP servers and plugin when prompted.
- [ ] `kitctl status --repo <pilot>`: every owned entry should read `ok`.

## Gaps that stay open by design

- Codex project trust and hook trust (2): the user's call, every time.
- Codex single-skill disables (4): Codex reads `[[skills.config]]` only from
  user config. Closing them needs a user-scope loadout, which would be a write
  to `~/.codex` and needs separate authorization.
