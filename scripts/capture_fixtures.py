"""Capture the *structure* of your Claude Code and Codex config as redacted test fixtures.

    uv run python scripts/capture_fixtures.py --out ./kitctl-fixtures   # from a checkout

Reads only. Writes only under --out. Every string value becomes a placeholder, numbers
become 0, and object keys that look like paths or secrets are replaced, so the output holds
key names, nesting, file layout, and record type names, never your values. Review the
output before sharing it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kitctl import paths as kit_paths  # noqa: E402
from kitctl.hashing import is_junk  # noqa: E402
from kitctl.secretscan import find  # noqa: E402

TYPE_KEYS = {"type", "kind", "source", "transport", "role", "event"}  # keep enum-like values
SESSION_SAMPLE = 200  # records per session file
MAX_FILES = 400
_PATHLIKE = re.compile(r"[/\\]|^[A-Za-z]:")


class Redactor:
    def __init__(self) -> None:
        self.keys: dict[str, str] = {}

    def key(self, key: str) -> str:
        if _PATHLIKE.search(key) or find(key) or len(key) > 80:
            return self.keys.setdefault(key, f"<key-{len(self.keys) + 1}>")
        return key

    def value(self, value: Any, key: str = "") -> Any:
        if isinstance(value, dict):
            return {self.key(str(k)): self.value(v, str(k)) for k, v in value.items()}
        if isinstance(value, list):
            return [self.value(v, key) for v in value[:3]]
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, (int, float)):
            return 0
        text = str(value)
        if key in TYPE_KEYS and re.fullmatch(r"[A-Za-z_][\w.-]{0,40}", text):
            return text  # enum-like discriminators (e.g. "tool_use", "McpToolCall")
        return "<str>"


def capture_json(source: Path, target: Path, redactor: Redactor) -> None:
    try:
        document = json.loads(source.read_text("utf-8"))
    except (OSError, ValueError):
        target.with_suffix(target.suffix + ".unreadable").write_text("")
        return
    target.write_text(json.dumps(redactor.value(document), indent=2) + "\n")


def capture_toml(source: Path, target: Path, redactor: Redactor) -> None:
    try:
        document = tomllib.loads(source.read_text("utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        target.with_suffix(".unreadable").write_text("")
        return
    target.with_name(target.name + ".json").write_text(
        json.dumps(redactor.value(document), indent=2) + "\n"
    )


def capture_markdown(source: Path, target: Path) -> None:
    text = source.read_text("utf-8", errors="replace")
    keys = []
    if text.startswith("---"):
        block = text.split("---", 2)[1] if text.count("---") >= 2 else ""
        keys = [
            line.split(":", 1)[0]
            for line in block.splitlines()
            if ":" in line and not line[:1].isspace()
        ]
    front = "".join(f"{k}: <str>\n" for k in keys)
    target.write_text((f"---\n{front}---\n" if keys else "") + "<body>\n")


def capture_jsonl(source: Path, target: Path, redactor: Redactor) -> None:
    out = []
    with source.open("rb") as handle:
        for index, line in enumerate(handle):
            if index >= SESSION_SAMPLE:
                break
            try:
                out.append(json.dumps(redactor.value(json.loads(line))))
            except ValueError:
                out.append('"<malformed>"')
    target.write_text("\n".join(out) + "\n")


def walk(base: Path, out: Path, redactor: Redactor, budget: list[int], sessions: int) -> None:
    session_files = 0
    for path in sorted(base.rglob("*")):
        if budget[0] <= 0:
            return
        relative = path.relative_to(base)
        if any(is_junk(part, True) for part in relative.parts[:-1]) or path.is_symlink():
            continue
        if not path.is_file() or is_junk(path.name, False):
            continue
        parts = set(relative.parts)
        if (
            parts & {"backups", "cache", "logs", "shell-snapshots", "statsig", "todos", "debug"}
            and "plugins" not in parts
        ):
            continue
        target = out / relative
        suffix = path.suffix.lower()
        if suffix == ".jsonl":
            if session_files >= sessions:
                continue
            session_files += 1
        if suffix not in {".json", ".toml", ".md", ".jsonl", ".rules"}:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if suffix == ".json":
            capture_json(path, target, redactor)
        elif suffix == ".toml":
            capture_toml(path, target, redactor)
        elif suffix == ".md":
            capture_markdown(path, target)
        elif suffix == ".jsonl":
            capture_jsonl(path, target, redactor)
        else:
            target.write_text("<rules>\n")
        budget[0] -= 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=Path("kitctl-fixtures"))
    parser.add_argument("--sessions", type=int, default=5, help="Session logs to sample.")
    args = parser.parse_args()
    paths = kit_paths.resolve()
    redactor = Redactor()
    budget = [MAX_FILES]
    sources = {
        "claude/home/.claude": paths.claude_config_dir,
        "codex/home/.codex": paths.codex_home,
        "shared/home/.agents": paths.agents_home,
    }
    for label, base in sources.items():
        if base.is_dir():
            walk(base, args.out / label, redactor, budget, args.sessions)
    if paths.claude_json.is_file():
        target = args.out / "claude/home/.claude.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        capture_json(paths.claude_json, target, redactor)
    files = [p for p in args.out.rglob("*") if p.is_file()]
    leaks = [p for p in files if find(p.read_text("utf-8", errors="replace"))]
    print(f"Wrote {len(files)} redacted files to {args.out} ({len(redactor.keys)} keys renamed).")
    if leaks:
        print(
            f"warning: {len(leaks)} files still look secret-shaped; not safe to share:",
            file=sys.stderr,
        )
        for leak in leaks:
            print(f"  {leak}", file=sys.stderr)
        return 1
    print("Review the files before sharing them; values are placeholders.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
