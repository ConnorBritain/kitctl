---
name: kit-forge
description: Research candidate skills, plugins, MCP servers and hooks for a Kit forge brief, then write a cited forge/<slug>/candidates.yaml. Use after `kitctl forge new <slug>` and before `kitctl forge propose <slug>`.
---
# Kit forge research

You turn a forge brief into `forge/<slug>/candidates.yaml`. You research and
judge. You never install, enable or run a candidate, and you never edit native
agent config. The deterministic commands (`kitctl scout`, `vendor`, `forge
propose`, `forge verify`, `plan`, `apply`) do everything that touches files or
code.

## Steps

1. Read `forge/<slug>/brief.yaml`: purpose, harnesses, target repos, constraints
   (license allow/deny, no telemetry, no runtime network, install scripts),
   required slots and exclusions.
2. Search for candidates with the playbook below. Keep a short list per slot.
3. Run `kitctl scout <url>…` on every candidate you might recommend. Scout
   clones into quarantine at a pinned commit and prints facts with `file:line`
   evidence. Do not read candidates any other way, and never run their code.
4. Read the quarantined clone (the `quarantine` path in the facts) to judge
   mechanism, overlap and opinion bias. Cite what you read as `path:line`.
5. Write `candidates.yaml` (schema below). Copy `facts` from the scout report
   unchanged, or leave them out. Every judgment cites evidence.
6. Run `kitctl forge propose <slug>` and read `gaps.md`. Fix rejected
   citations, or change verdicts, until it passes.
7. Hand the result to the owner. Nothing leaves `inbox/` without their review.

## Rubric (score every candidate on all ten)

| Criterion | Ask | Typical evidence |
|---|---|---|
| License | Is it SPDX-clear and allowed by the brief? | `fact:license`, `LICENSE:1` |
| Maintenance | Last commit, open issues, releases or tags to pin | `fact:last_commit`, release page URL |
| Mechanism | What does it actually do: skill text, MCP tools, hook command? | `SKILL.md:12`, `src/server.ts:40` |
| Harness compatibility | Claude, Codex or both, and in which manifest format? | `fact:manifests`, `.codex-plugin/plugin.json:1` |
| Overlap | Does it duplicate something already in the target loadout or another candidate? | file:line on both sides |
| Opinion bias | Does it impose its own look, voice or taste (`none`/`low`/`high`)? | lines naming its defaults, palettes or house style |
| Network and telemetry | Is anything on by default, and is there an opt-out flag? | `fact:telemetry`, `fact:outbound_network`, the flag's line |
| Install scripts | postinstall, curl-pipe installers | `fact:install_scripts` |
| Trigger aggressiveness | "MANDATORY", "always use", "execute first" | `fact:aggressive_triggers` |
| Pinnability | Can it be pinned (package version, tag, sha)? | `package.json:3`, tag URL |

**Opinion bias decides most cases.** Prefer one generator and many checkers.
A tool that brings its own aesthetic or voice competes with the project's
sources of truth, even when it is good.

## Search playbook

- GitHub code and repo search for the capability (`SKILL.md` plus a topic,
  `mcp server <topic>`), sorted by recent activity, not stars.
- Plugin marketplaces: `claude plugin marketplace list` sources, Codex
  marketplace repos, and curated awesome-lists. Treat lists as leads, not
  evidence.
- Vendor documentation for official MCP servers, and their data-collection
  sections.
- When the product has its own site or handbook repo, clone it with scout and
  measure the real artifacts instead of trusting screenshots.
- Search for known conflicts: other tools in the same slot that the brief
  must turn off.

## candidates.yaml

```yaml
schema_version: 1
candidates:
  - id: github.com/owner/repo@<40-hex sha>   # exactly as scout printed it
    source: { url: https://github.com/owner/repo, sha: <40-hex sha> }
    facts: { license: MIT }                  # optional; must equal scout's values
    fills: [browser.mcp]
    overlaps: []
    opinion_bias: none                       # none | low | high
    conflicts: []
    verdict: adopt                           # adopt | vendor | borrow-ideas | avoid
    config:                                  # required for adopt and vendor
      kind: mcp                              # skill | mcp | hook | plugin | marketplace
      name: chrome-devtools
      command: npx
      pin: { package: chrome-devtools-mcp, version: 1.11.0 }
      args: [--headless, --isolated, --no-usage-statistics]
      # vendor also needs: path: skills/the-skill
    evidence:
      fills: [src/tools/lighthouse.ts:12]
      opinion_bias: [README.md:30]
      verdict: [fact:license, README.md:88]
      config: [README.md:95, https://www.npmjs.com/package/chrome-devtools-mcp]
    notes: one line for gaps.md when the verdict is borrow-ideas
```

## The cite-everything rule

- Every stated judgment (`fills`, `overlaps`, `opinion_bias`, `verdict`,
  `config`) needs at least one citation. A judgment of `none` counts as stated.
- A citation is one of:
  - `path:line` inside the quarantined clone (the line must exist);
  - `fact:<key>` from the scout report;
  - an `https://` page you actually read.
- `kitctl forge propose` rejects uncited claims, citations past the end of a
  file, facts that differ from scout, and SHAs that don't match quarantine.
- If you can't cite it, don't claim it. Write `borrow-ideas` with a note, or
  leave the candidate out.

## Worked example

`references/worked-example.md` summarises the brand research behind the
brand-studio loadout. The originals are in the brand-builder repository under
`research/companions/`.
