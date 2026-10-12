"""Print the CHANGELOG.md section for a version (used as GitHub release notes).

    python scripts/release_notes.py 0.1.0rc1

Exits 1 if CHANGELOG.md has no ``## <version>`` section, so a release can't ship
without notes.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def section(text: str, version: str) -> str | None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("## ") and line[3:].split()[0:1] == [version]:
            body = []
            for following in lines[index + 1 :]:
                if following.startswith("## "):
                    break
                body.append(following)
            return "\n".join(body).strip() + "\n"
    return None


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: release_notes.py <version>", file=sys.stderr)
        return 2
    notes = section((ROOT / "CHANGELOG.md").read_text("utf-8"), sys.argv[1])
    if notes is None:
        print(f"CHANGELOG.md has no '## {sys.argv[1]}' section", file=sys.stderr)
        return 1
    sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
