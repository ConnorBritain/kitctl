# Real-environment runbook (v3)

Paste the prompt for your OS into a Claude Code or Codex session on that machine. It tests
the development branch with `uvx` (nothing installed globally) and writes a results file
to send back.

v3 adds the write side in two stages:

- **Stage 1 (sandbox on your machine):** copies of your real config files in a
  throwaway home. The prompt runs apply, re-apply, purge, and eject there and compares
  bytes. Your real config is never written.
- **Stage 2 (pilot):** one repository you choose. The prompt applies, checks, and
  purges it, and only after you say yes.

The read-side checks from v2 are kept as Part A.

Close Claude Code and Codex desktop apps first and run from a plain terminal session: a
running harness writes its own state while you watch.

## macOS

````text
Test the kitctl development build on this Mac. Repo https://github.com/ConnorBritain/kitctl,
branch claude/new-session-sj8so6. Never print config values, secrets, tokens, prompt text
or env values: names, counts, paths, versions only. Do not run any kitctl command that
writes agent configuration outside the sandbox in Part B unless I approve Part C.

KIT='uvx --from git+https://github.com/ConnorBritain/kitctl@claude/new-session-sj8so6 kitctl'
Results go in ~/kitctl-results-macos-v3.md; working files in /tmp/kitctl-run3/.

PART A - read side (unchanged from v2, shortened)
1. Versions: sw_vers; uname -m; uv --version; claude --version; codex --version; $KIT --version.
2. Snapshot (shasum -a 256, files only) of ~/.claude.json, ~/.claude/{settings.json,
   settings.local.json,CLAUDE.md,skills,agents,commands,rules,hooks},
   ~/.claude/plugins/{installed_plugins.json,known_marketplaces.json},
   ~/.codex/{config.toml,hooks.json,AGENTS.md,AGENTS.override.md,rules,skills}, ~/.agents.
   Snapshot A, wait 60 s, snapshot B (control). Snapshot C, then run: $KIT inventory
   --timings (twice), $KIT usage (twice), $KIT report --format html, $KIT doctor,
   $KIT validate, $KIT status, $KIT desired. Snapshot D. Changes C->D not in A->B are
   critical bugs: list them.

PART B - write side in a sandbox (real files are only read)
3. S=/tmp/kitctl-run3/sandbox; mkdir -p "$S"/home. Copy (cp -p, keep structure) into
   "$S"/home: ~/.claude/settings.json -> .claude/settings.json, ~/.claude.json ->
   .claude/.claude.json, ~/.codex/config.toml, ~/.codex/hooks.json, ~/.codex/AGENTS.md
   (each only if it exists), and copy the warehouse: cp -R ~/kits "$S"/home/kits.
   Make a scratch repo: mkdir -p "$S"/repo && git -C "$S"/repo init -q.
   Define: SB="env HOME=$S/home CLAUDE_CONFIG_DIR=$S/home/.claude CODEX_HOME=$S/home/.codex
   XDG_STATE_HOME=$S/state XDG_CONFIG_HOME=$S/config XDG_CACHE_HOME=$S/cache"
   Every command in Part B runs as: $SB $KIT ...
4. Warehouse content: if ~/kits has no loadouts yet, create in the sandbox copy only:
   - adopt two of my skills and one MCP server from the REAL inventory into the sandbox
     warehouse: $SB $KIT adopt <path to a real skill dir> --to primitives --warehouse
     "$S"/home/kits (reads the real file, writes only the sandbox), and for MCP use
     `$SB $KIT adopt mcp:<name> --to primitives` after copying ~/.claude.json as above.
   - write "$S"/home/kits/loadouts/core.toml listing one skill and the server, and
     loadouts/pilot.toml with the other skill; write "$S"/repo/kit.toml with
     loadouts = ["pilot"].
   Run $SB $KIT validate and $SB $KIT desired --repo "$S"/repo.
5. Baseline: hash every file under "$S"/home/.claude, .codex, .agents and "$S"/repo
   (exclude "$S"/home/kits) -> base.sha256.
6. $SB $KIT apply --global --dry-run and $SB $KIT apply --repo "$S"/repo --dry-run:
   save the output (it shows names and hashes only; check that before saving, and redact
   anything that looks like a value). Re-hash: must equal base.sha256 (dry runs write nothing).
7. $SB $KIT apply --global --yes; $SB $KIT apply --repo "$S"/repo --yes; $SB $KIT status.
   Check: Codex config.toml comments still present (count lines starting with #, before
   and after); the MCP server appears in .claude/.claude.json and config.toml with ${NAME}
   references (report key names only); skills exist under .claude/skills and .agents/skills.
8. Re-apply both: each must say "Nothing to change."
9. Edit the copied settings.json by hand (add a harmless key "kitctlTest": true), then
   $SB $KIT purge --all --yes. Re-hash: every file must equal base.sha256 except
   settings.json, which must differ only by your added key (remove it and compare).
10. Apply both again, then $SB $KIT eject --yes. Check: no kitctl markers in AGENTS.md,
   no kit.lock, $SB $KIT status says it owns nothing, the installed skills still exist.
11. Capture hooks, in the sandbox only: $SB $KIT capture install --command "$KIT" --yes
   (shows the hook entries), then create a new skill dir under "$S"/home/.claude/skills,
   run `echo '{"cwd":"'$S'/repo"}' | $SB $KIT reconcile --hook claude` and confirm it lands
   in "$S"/home/kits/inbox. Time `$SB $KIT reconcile --hook claude` with nothing new
   (3 runs; note this includes uvx overhead). Then $SB $KIT capture remove --yes.
12. Marketplace: $SB $KIT marketplace build; then claude plugin validate
   "$S"/home/kits/marketplace (with HOME="$S"/home). Report the result line.
13. Delete "$S" when done (it holds copies of your config).

PART C - pilot repository (ask me first, and ask which repo)
14. Only after I name a repo and say go: in that repo, write kit.toml with one loadout,
   run $KIT apply --repo . --dry-run and show me the plan; apply only when I confirm.
   Then: claude mcp list (in the repo) and codex mcp list show the server; start a
   Claude Code session and confirm the skill is listed; git status shows only the files
   the plan named plus kit.lock. Finally $KIT purge --repo . --yes and confirm git status
   is back to what it was.

Write ~/kitctl-results-macos-v3.md with versions, Part A timings and snapshot diff,
every Part B check (pass/fail, exit codes, file counts, timings), any conflict or gap
messages verbatim (they contain no values), Part C results if run, and errors. Show me
the file at the end.
````

## Windows

````text
Test the kitctl development build on this Windows machine using PowerShell. Repo
https://github.com/ConnorBritain/kitctl, branch claude/new-session-sj8so6. Never print
config values, secrets, tokens, prompt text or env values. Do not run any kitctl command
that writes agent configuration outside the sandbox in Part B unless I approve Part C.

function kit { uvx --from git+https://github.com/ConnorBritain/kitctl@claude/new-session-sj8so6 kitctl @args }
Results go in %USERPROFILE%\kitctl-results-windows-v3.md; working files in $env:TEMP\kitctl-run3\.

PART A - read side
1. Versions ([Environment]::OSVersion, $env:PROCESSOR_ARCHITECTURE, uv, claude, codex,
   kit --version); whether symlinks can be created; whether %USERPROFILE%\.claude, .codex
   or .agents are links.
2. Snapshot with Get-FileHash -Algorithm SHA256 of the files kitctl reads (as in v2):
   control A/B 60 s apart, then C, run kit init (ask me where repos live), kit inventory
   --timings (twice), kit usage (twice), kit report --format html, kit doctor, kit
   validate, kit status, kit desired; snapshot D. Changes not in the control are critical.
3. Windows checks: drive-letter paths with no doubled separators, no repo listed twice by
   case, links/junctions shown as kind "link", long-path or locked-file errors.

PART B - write side in a sandbox
4. $S="$env:TEMP\kitctl-run3\sandbox". Copy into $S\home: .claude\settings.json,
   .claude.json -> .claude\.claude.json, .codex\config.toml, .codex\hooks.json,
   .codex\AGENTS.md (if present), and the warehouse to $S\home\kits. Create $S\repo with
   git init.
   function sb { $env:HOME="$S\home"; $env:USERPROFILE="$S\home";
     $env:CLAUDE_CONFIG_DIR="$S\home\.claude"; $env:CODEX_HOME="$S\home\.codex";
     $env:APPDATA="$S\appdata"; $env:LOCALAPPDATA="$S\localappdata"; kit @args }
   Run Part B commands as `sb ...` in a separate PowerShell window you close afterwards
   (sb changes that session's environment).
5. Then follow macOS steps 4-12 with sb. Also check:
   - CRLF: if a copied file uses CRLF line endings, it keeps them after apply and purge.
   - Hook commands in settings.json and hooks.json use quoted Windows paths that run
     (execute the hook command line once by hand with an empty stdin).
   - Skill folders were copied, not linked.
6. Delete $S when done.

PART C - pilot (ask me first), as macOS step 14.

Write %USERPROFILE%\kitctl-results-windows-v3.md with the same sections plus the Windows
checks. Show me the file at the end.
````
