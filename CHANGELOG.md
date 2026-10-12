# Changelog

## Unreleased (0.1.0 track)

- Links are first-class inventory entries with `link_target`; kitctl reads through them
  only into allowed roots. Windows junctions are handled; broken links are findings.
- `hash_scheme` 2: CRLF/CR normalized for text, junk files skipped, NFC POSIX paths.
- New `paths` module honors `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `HOME`/`USERPROFILE`, XDG.
- Single-source version in `kitctl.__init__` with tested PEP 440 to semver mapping; the
  npm launcher locates the wheel by pattern.
- Secret detection adds Anthropic, OpenAI, Slack, Azure, JWT, and Bearer shapes and
  guards inventory output.
- Report schema 2 (`findings` replaces `warnings`); exit 1 only for error findings.
- CI adds macOS.

## 0.0.1 — 2026-10-11

- Initial Python CLI with version, help, and explicitly scoped read-only inventory.
- Detect skill folders and config locations; compute hashes without emitting contents.
- npm launcher bundles the same Python wheel and runs it through uvx.
- Sandbox tests, Linux/Windows CI, MIT license, and phased implementation plan.

This release does not change native agent configuration.
