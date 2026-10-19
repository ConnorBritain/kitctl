# Worked example: the brand-studio stack

Source: brand-builder `research/companions/` (README, design-tools, writing-tools),
2026-10-19. 69 repositories were cloned read-only and scored with this rubric.

- **Principle found.** Most design and writing skills supply a look or a voice.
  Running two generators lets the more popular default win. So the stack has one
  generator (brand-builder) and many checkers.
- **Adopted.** Chrome DevTools MCP 1.11.0 (Apache-2.0) with `--headless
  --isolated --no-usage-statistics --no-performance-crux`: telemetry was on by
  default and mitigated by a flag. Adobe Leonardo MCP 0.1.0 (Apache-2.0): it
  builds palettes from the brand's own colors and never proposes hues.
- **Vendored with notices.** Two MIT components, rewritten to remove their own
  house style.
- **Avoided, and disabled by the loadout.** Anthropic `brand-guidelines` and
  `theme-factory` (apply someone else's palette), `frontend-design` (a second
  generator), Impeccable (its own hook, plus remote calls by default),
  taste-skill, Hallmark, ui-ux-pro-max (category defaults), and GoogleChrome
  `modern-web-guidance` (declares itself "MANDATORY: execute first").
- **Borrowed ideas.** Interface guidelines with their design and copy sections
  removed. A "fetch rules at runtime" skill, pinned to a local copy instead.

Each of these findings maps to a scout fact: telemetry default, install script,
runtime self-fetch, aggressive trigger, or missing license. They map to a
rubric row too: opinion bias, or overlap with the generator slot.
