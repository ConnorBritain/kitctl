# Real-environment runbook (v2)

Paste the prompt for your OS into a Claude Code or Codex session on that machine. It tests
the development branch with `uvx` (nothing installed globally) and writes a results file
to send back. Changes from v1: a control snapshot to separate harness background activity
from kitctl, a diff limited to files kitctl reads, `inventory --timings`, `kitctl
conformance`, a Codex log-shape question, and optional fixture capture.

Best results: close Claude Code and Codex desktop apps first, and run the prompt from a
plain terminal session rather than from inside the harness being measured. A harness that
is running writes its own sessions, logs, and databases while you watch.

## macOS

````text
Test the kitctl development build on this Mac. Repo https://github.com/ConnorBritain/kitctl,
branch claude/new-session-sj8so6. kitctl must not modify agent configuration; it may write
only ~/.config/kitctl/, ~/Library/Caches/kitctl/ and report files. Never print config
values, secrets, tokens, prompt text or env values: names, counts, paths, versions only.

1. Versions: sw_vers; uname -m; uv --version; claude --version; codex --version.
   KIT='uvx --from git+https://github.com/ConnorBritain/kitctl@claude/new-session-sj8so6 kitctl'
   Run $KIT --version (first run installs into uv's cache).
2. Snapshot scope (files kitctl reads, not harness runtime state):
   ~/.claude.json, ~/.claude/{settings.json,settings.local.json,CLAUDE.md}, ~/.claude/skills,
   ~/.claude/agents, ~/.claude/commands, ~/.claude/rules, ~/.claude/hooks,
   ~/.claude/plugins/{installed_plugins.json,known_marketplaces.json},
   ~/.codex/{config.toml,hooks.json,AGENTS.md,AGENTS.override.md}, ~/.codex/rules,
   ~/.codex/skills, ~/.agents. Hash with shasum -a 256 (files only, existing paths only).
3. Control: snapshot A, wait 60 s doing nothing, snapshot B. Diff A vs B = background churn.
4. Snapshot C. Then run, timing each with `time` and saving output under /tmp/kitctl-run2/:
   $KIT init --force --non-interactive --repo-parent ~/Code --warehouse ~/kits
   $KIT inventory --timings > inventory.txt 2> timings-cold.txt
   $KIT inventory --timings --json > inventory.json 2> timings-warm.txt   (repeat twice more)
   $KIT usage > usage.txt ; $KIT usage --json > usage.json (run twice; time the second)
   $KIT report --format html ; $KIT report --format md ; $KIT report --format json
   $KIT report --format json --no-descriptions (confirm no "description" keys)
   $KIT doctor > doctor.txt ; $KIT validate > validate.txt
   Snapshot D. Diff C vs D. Any change that is not also in the A/B control diff is a
   critical kitctl bug: list it. Note `kitctl --version` timing as the uvx overhead.
5. Conformance: run `$KIT conformance` in ~/Code/kitctl and in two other repos. Ask me
   before running `$KIT conformance --health-checks` (it starts MCP servers). Report every
   "unexplained" difference with item, check and missing side.
6. Codex log question (keys and ids only, no content): in 3 recent files under
   ~/.codex/sessions, for each response_item function_call that has a `namespace`, is there
   an event_msg item_completed McpToolCall whose item.id equals the function_call's call_id?
   Report counts: matched, unmatched function_calls, unmatched McpToolCalls. Also report
   the namespace value format (e.g. "mcp__server" or "server"), with server names
   replaced by placeholders if they're sensitive.
7. Codex plugin enabled state: does ~/.codex/config.toml contain [plugins."<id>"] tables
   with an `enabled` key (yes/no, count)? Compare with `codex plugin list --json` enabled
   flags by id only.
8. Review the HTML report: are Codex plugins, their skills and MCP servers, and Codex MCP
   usage now present? Is the "Used but not in the current inventory" list sensible? Any
   finding that looks wrong?
9. Optional fixture capture (ask me first): git clone --depth 1 --branch
   claude/new-session-sj8so6 https://github.com/ConnorBritain/kitctl /tmp/kitctl-src and
   run `uv run --project /tmp/kitctl-src python /tmp/kitctl-src/scripts/capture_fixtures.py
   --out ~/kitctl-fixtures-macos`. Report the file count and whether it warned; don't
   share the files unless I say so.

Write ~/kitctl-results-macos-v2.md with versions, timings (cold/warm, and the --timings
phase lines), exit codes, control-vs-run snapshot diff, conformance differences, the
Codex answers, report observations, and errors. Show me the file at the end.
````

## Windows

````text
Test the kitctl development build on this Windows machine using PowerShell. Repo
https://github.com/ConnorBritain/kitctl, branch claude/new-session-sj8so6. kitctl must not
modify agent configuration; it may write only %APPDATA%\kitctl\, %LOCALAPPDATA%\kitctl\Cache\
and report files. Never print config values, secrets, tokens, prompt text or env values.

1. Versions: [Environment]::OSVersion; $env:PROCESSOR_ARCHITECTURE; uv --version;
   claude --version; codex --version. Note whether symlinks can be created (Developer
   Mode) and whether %USERPROFILE%\.claude, .codex or .agents are links (Get-Item | Select
   LinkType, Target).
   function kit { uvx --from git+https://github.com/ConnorBritain/kitctl@claude/new-session-sj8so6 kitctl @args }
   Run kit --version.
2. Snapshot scope (files kitctl reads): %USERPROFILE%\.claude.json and, under
   %USERPROFILE%\.claude: settings.json, settings.local.json, CLAUDE.md, skills, agents,
   commands, rules, hooks, plugins\installed_plugins.json, plugins\known_marketplaces.json;
   under %USERPROFILE%\.codex: config.toml, hooks.json, AGENTS.md, AGENTS.override.md,
   rules, skills; and %USERPROFILE%\.agents. Hash with Get-FileHash -Algorithm SHA256.
3. Control: snapshot A, Start-Sleep 60, snapshot B, Compare-Object = background churn.
4. Snapshot C. Then run with Measure-Command, saving output under $env:TEMP\kitctl-run2\:
   kit init --force --non-interactive --repo-parent <where my repos live; ask me> --warehouse $env:USERPROFILE\kits
   kit inventory --timings (cold), then kit inventory --timings --json three times (warm)
   kit usage twice (time the second); kit report --format html/md/json;
   kit report --format json --no-descriptions; kit doctor; kit validate
   Snapshot D; Compare-Object C vs D. Changes not in the A/B control are critical bugs.
5. Windows checks: paths in inventory.json render with drive letters and no doubled
   separators; no repo listed twice with different letter case; symlinks/junctions appear
   as kind "link" with a target; any long-path (>260), locked-file or permission errors.
6. Conformance: kit conformance in two repos; ask me before --health-checks. Report
   unexplained differences.
7. Same Codex questions as macOS: namespaced function_call call_id vs McpToolCall item.id
   matching counts and namespace format; [plugins."<id>"] enabled tables vs
   `codex plugin list --json`; where Codex keeps config, sessions and plugins on Windows.
8. Review the HTML report as on macOS.
9. Optional fixture capture (ask me first): clone the branch to $env:TEMP\kitctl-src and
   run uv run --project $env:TEMP\kitctl-src python $env:TEMP\kitctl-src\scripts\capture_fixtures.py --out $env:USERPROFILE\kitctl-fixtures-windows

Write %USERPROFILE%\kitctl-results-windows-v2.md with the same sections as macOS plus the
Windows checks. Show me the file at the end.
````
