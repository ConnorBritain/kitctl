"""Benchmark kitctl against a synthetic environment.

    uv run python scripts/bench.py                    # 50 repos x 30 skills, 200 MB logs
    uv run python scripts/bench.py --small            # quick smoke size
    uv run python scripts/bench.py --json out.json --compare docs/performance.json

Each command runs in a fresh interpreter (like a real CLI call). "Cold" means no
kitctl cache; the OS file cache is warm after fixture generation in both cases.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BUDGETS_MS = {"version": 100, "inventory_cold": 2000, "inventory_warm": 500, "usage_warm": 500}


def generate(base: Path, repos: int, skills: int, transcript_mb: int) -> dict[str, str]:
    rng = random.Random(7)
    home = base / "home"
    claude = home / ".claude"
    for index in range(skills):
        skill = claude / "skills" / f"user-skill-{index}"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: user-skill-{index}\ndescription: d\n---\nbody\n"
        )
    (claude / "settings.json").write_text('{"enabledPlugins": {}}')
    code = home / "Code"
    for r in range(repos):
        repo = code / f"repo-{r:03d}"
        (repo / ".git").mkdir(parents=True)
        (repo / "CLAUDE.md").write_text("Project rules\n")
        (repo / ".mcp.json").write_text('{"mcpServers": {"gh": {"command": "github-mcp"}}}')
        for s in range(skills):
            skill = repo / ".claude/skills" / f"skill-{s}"
            (skill / "scripts").mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                f"---\nname: skill-{s}\ndescription: d\n---\n{'x' * 400}\n"
            )
            (skill / "scripts/run.py").write_text("print('hi')\n" * 20)
        (repo / "src/pkg").mkdir(parents=True)
        (repo / "node_modules/dep").mkdir(parents=True)
    projects = claude / "projects"
    target = transcript_mb * 1024 * 1024
    written = 0
    session = 0
    filler = "lorem ipsum " * 150
    while written < target:
        folder = projects / f"-repo-{session % max(repos, 1):03d}"
        folder.mkdir(parents=True, exist_ok=True)
        cwd = str(code / f"repo-{session % max(repos, 1):03d}")
        with (folder / f"session-{session}.jsonl").open("w", encoding="utf-8") as handle:
            for line_no in range(2000):
                ts = f"2026-10-{1 + line_no % 9:02d}T10:{line_no % 60:02d}:00Z"
                roll = rng.random()
                if roll < 0.05:
                    block = {
                        "type": "tool_use",
                        "name": "Skill",
                        "input": {"skill": f"skill-{rng.randrange(skills)}"},
                    }
                elif roll < 0.10:
                    block = {"type": "tool_use", "name": "mcp__gh__search", "input": {"q": "x"}}
                elif roll < 0.20:
                    block = {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}
                else:
                    block = {"type": "text", "text": filler}
                record = {
                    "type": "assistant",
                    "timestamp": ts,
                    "cwd": cwd,
                    "message": {"content": [block]},
                }
                line = json.dumps(record) + "\n"
                handle.write(line)
                written += len(line)
                if written >= target:
                    break
        session += 1
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "CODEX", "XDG_"))}
    env.update(HOME=str(home), USERPROFILE=str(home))
    return env


CHILD = """
import resource, sys, time
from kitctl.cli import main
start = time.perf_counter()
try:
    code = main(sys.argv[2:])
except SystemExit as exit:
    code = exit.code
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
sys.stderr.write(f"\\nBENCH_RSS={peak}\\n")
"""


def timed(args: list[str], env: dict[str, str]) -> tuple[float, int | None]:
    if os.name == "nt":
        command = [sys.executable, "-m", "kitctl", *args]
    else:
        command = [sys.executable, "-c", CHILD, "--", *args]
    start = time.perf_counter()
    result = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
    elapsed = (time.perf_counter() - start) * 1000
    if result.returncode not in (0, 1):
        raise SystemExit(f"kitctl {' '.join(args)} failed: {result.stderr[-500:]}")
    rss = None
    for line in result.stderr.splitlines():
        if line.startswith("BENCH_RSS="):
            value = int(line.split("=", 1)[1])
            rss = value // 1024 if sys.platform != "darwin" else value // (1024 * 1024)
    return elapsed, rss


def best(args: list[str], env: dict[str, str], runs: int = 3) -> tuple[float, int | None]:
    results = [timed(args, env) for _ in range(runs)]
    return min(r[0] for r in results), max((r[1] or 0) for r in results) or None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repos", type=int, default=50)
    parser.add_argument("--skills", type=int, default=30)
    parser.add_argument("--transcript-mb", type=int, default=200)
    parser.add_argument("--small", action="store_true")
    parser.add_argument("--json", type=Path)
    parser.add_argument("--compare", type=Path, help="Baseline JSON; flag >25%% regressions.")
    args = parser.parse_args()
    if args.small:
        args.repos, args.skills, args.transcript_mb = 5, 5, 5
    base = Path(tempfile.mkdtemp(prefix="kitctl-bench-"))
    try:
        start = time.perf_counter()
        env = generate(base, args.repos, args.skills, args.transcript_mb)
        print(
            f"fixture: {args.repos} repos x {args.skills} skills, {args.transcript_mb} MB logs "
            f"({time.perf_counter() - start:.1f} s to generate)"
        )
        subprocess.run(
            [sys.executable, "-m", "kitctl", "init", "--non-interactive"],
            env=env,
            capture_output=True,
            check=True,
        )
        cache = base / "home/.cache/kitctl"
        results: dict[str, dict[str, float | int | None]] = {}

        def record(name: str, ms: float, rss: int | None) -> None:
            results[name] = {"ms": round(ms, 1), "peak_rss_mb": rss}
            budget = BUDGETS_MS.get(name)
            verdict = "" if budget is None else (" ok" if ms <= budget else f" OVER {budget} ms")
            print(f"{name:16} {ms:8.0f} ms  rss {rss or '?':>5} MB{verdict}")

        record("version", *best(["--version"], env, 5))
        shutil.rmtree(cache, ignore_errors=True)
        record("inventory_cold", *timed(["inventory", "--json"], env))
        record("inventory_warm", *best(["inventory", "--json"], env))
        shutil.rmtree(cache, ignore_errors=True)
        record("usage_cold", *timed(["usage", "--json"], env))
        record("usage_warm", *best(["usage", "--json"], env))
        record("report_warm", *best(["report", "--format", "html"], env, 2))
    finally:
        shutil.rmtree(base, ignore_errors=True)
    payload = {
        "size": {"repos": args.repos, "skills": args.skills, "transcript_mb": args.transcript_mb},
        "results": results,
    }
    if args.json:
        args.json.write_text(json.dumps(payload, indent=2) + "\n")
    regressions = []
    if args.compare and args.compare.is_file():
        baseline = json.loads(args.compare.read_text()).get("results", {})
        for name, value in results.items():
            before = baseline.get(name, {}).get("ms")
            if before and value["ms"] and value["ms"] > before * 1.25:
                regressions.append(f"{name}: {value['ms']:.0f} ms vs baseline {before:.0f} ms")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("## kitctl benchmark\n\n| step | ms | peak RSS MB |\n|---|---:|---:|\n")
            for name, value in results.items():
                handle.write(f"| {name} | {value['ms']} | {value['peak_rss_mb']} |\n")
            if regressions:
                handle.write(
                    "\n**Regressions over 25%:**\n\n" + "".join(f"- {r}\n" for r in regressions)
                )
    for regression in regressions:
        print(f"regression: {regression}")
    return 1 if regressions else 0


if __name__ == "__main__":
    raise SystemExit(main())
