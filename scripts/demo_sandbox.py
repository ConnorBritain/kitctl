"""Build a throwaway Mac-like HOME and run every kitctl command against it, write side included.

    uv run python scripts/demo_sandbox.py [--keep] [--repos N]

Uses the synthetic fixtures in tests/fixtures; touches nothing outside a temp dir.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures"


def expand(source: Path, target: Path, tokens: dict[str, str]) -> None:
    for path in source.rglob("*"):
        if path.is_file():
            destination = target / path.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            data = path.read_bytes()
            for token, value in tokens.items():
                data = data.replace(token.encode(), value.encode())
            destination.write_bytes(data)


def build(base: Path, repo_count: int) -> dict[str, str]:
    home = base / "home"
    code = home / "Code"
    first = code / "app-0"
    tokens = {
        "{{HOME}}": home.as_posix(),
        "{{REPO}}": first.as_posix(),
        "{{PLUGINS}}": (home / ".claude/plugins").as_posix(),
        "{{CANARY}}": "demo-placeholder",
    }
    for name in ("claude/2.1.296", "codex/synthetic"):
        expand(FIXTURES / name / "home", home, tokens)
    # Default Claude layout keeps .claude.json in HOME (no CLAUDE_CONFIG_DIR).
    shutil.move(home / ".claude/.claude.json", home / ".claude.json")
    for index in range(repo_count):
        repo = code / f"app-{index}"
        for name in ("claude/2.1.296", "codex/synthetic"):
            expand(FIXTURES / name / "repo", repo, tokens)
        (repo / ".git").mkdir(parents=True, exist_ok=True)
    (code / "notes").mkdir(parents=True)  # not a repo
    (home / "kits/inbox").mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "CODEX", "XDG_"))}
    env.update(HOME=str(home), USERPROFILE=str(home))
    return env


def write_core(home: Path) -> None:
    loadouts = home / "kits/loadouts"
    loadouts.mkdir(parents=True, exist_ok=True)
    (loadouts / "core.toml").write_text(
        'schema_version = 1\nname = "core"\nskills = ["writer"]\nmcp = ["sentry"]\n',
        encoding="utf-8",
    )


def fingerprint(roots: list[Path]) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    for root in roots:
        paths = [root] if root.is_file() else sorted(root.rglob("*")) if root.exists() else []
        for path in paths:
            found[str(path)] = path.read_bytes() if path.is_file() else b"<dir>"
    return found


def run(command: list[str], env: dict[str, str]) -> tuple[float, subprocess.CompletedProcess[str]]:
    start = time.perf_counter()
    result = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
    return (time.perf_counter() - start) * 1000, result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--repos", type=int, default=3)
    args = parser.parse_args()
    base = Path(tempfile.mkdtemp(prefix="kitctl-demo-"))
    try:
        env = build(base, args.repos)
        kitctl = [sys.executable, "-m", "kitctl"]
        elapsed, result = run([*kitctl, "init", "--non-interactive"], env)
        print(f"$ kitctl init --non-interactive   ({elapsed:.0f} ms)")
        print(result.stdout + result.stderr)
        elapsed, result = run([*kitctl, "inventory"], env)
        shown = result.stdout.replace(str(base), "<sandbox>")
        print(f"$ kitctl inventory   (cold, {elapsed:.0f} ms, exit {result.returncode})\n{shown}")
        print(result.stderr.replace(str(base), "<sandbox>"))
        # Transcripts come from the fixtures (attributed to app-0).
        for command in (
            ["usage"],
            ["report", "--format", "html"],
            ["doctor"],
            ["triage", "--list"],
            ["triage", "--accept-proposals"],
            ["plan"],
            ["adopt", "mcp:sentry"],
            ["validate"],
        ):
            elapsed, result = run([*kitctl, *command], env)
            print(f"$ kitctl {' '.join(command)}   ({elapsed:.0f} ms, exit {result.returncode})")
            print((result.stdout + result.stderr).replace(str(base), "<sandbox>"))
        # Write side: promote two primitives, declare core, apply, re-apply, purge.
        home = Path(env["HOME"])
        watched = [home / ".claude", home / ".claude.json", home / ".codex", home / ".agents"]
        write_core(home)
        for command in (
            [
                "adopt",
                str(home / ".claude/skills/writer"),
                "--to",
                "primitives",
                "--name",
                "writer",
            ],
            ["adopt", "mcp:sentry", "--to", "primitives"],
            ["apply", "--global", "--dry-run"],
        ):
            elapsed, result = run([*kitctl, *command], env)
            print(f"$ kitctl {' '.join(command)}   ({elapsed:.0f} ms, exit {result.returncode})")
            print((result.stdout + result.stderr).replace(str(base), "<sandbox>"))
        # The user's sentry entry holds a literal credential, so apply refuses to take it over.
        # Simulate the migration the conflict asks for: the user removes their entry.
        claude_json = home / ".claude.json"
        data = json.loads(claude_json.read_text("utf-8"))
        data["mcpServers"].pop("sentry")
        claude_json.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print("(user removes their literal-credential sentry entry, as the conflict asks)\n")
        baseline = fingerprint(watched)
        for command in (
            ["desired"],
            ["apply", "--global", "--dry-run"],
            ["apply", "--global", "--yes"],
            ["status"],
            ["apply", "--global", "--dry-run"],
            ["purge", "--all", "--yes"],
        ):
            elapsed, result = run([*kitctl, *command], env)
            print(f"$ kitctl {' '.join(command)}   ({elapsed:.0f} ms, exit {result.returncode})")
            print((result.stdout + result.stderr).replace(str(base), "<sandbox>"))
        restored = fingerprint(watched) == baseline
        print(f"purge restored every config file byte for byte: {restored}")
        timings = [run([*kitctl, "inventory"], env)[0] for _ in range(3)]
        shown_timings = ", ".join(f"{t:.0f} ms" for t in timings)
        print(f"warm runs: {shown_timings} (includes interpreter start)")
        if args.keep:
            print(f"Sandbox kept at {base}")
    finally:
        if not args.keep:
            shutil.rmtree(base, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
