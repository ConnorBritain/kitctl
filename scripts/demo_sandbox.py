"""Build a throwaway Mac-like HOME and run `kitctl init` + `kitctl inventory` against it.

    uv run python scripts/demo_sandbox.py [--keep] [--repos N]

Uses the synthetic fixtures in tests/fixtures; touches nothing outside a temp dir.
"""

from __future__ import annotations

import argparse
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
