"""Small defense-in-depth check; never print matching secret values."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"gh[pousr]_" + rb"[A-Za-z0-9_]{36,}"),
    re.compile(rb"github_pat_" + rb"[A-Za-z0-9_]{50,}"),
    re.compile(rb"pypi-" + rb"[A-Za-z0-9_-]{40,}"),
    re.compile(rb"npm_" + rb"[A-Za-z0-9]{36,}"),
    re.compile(rb"AKIA" + rb"[A-Z0-9]{16}"),
    re.compile(rb"sk-(?:live|proj)-" + rb"[A-Za-z0-9_-]{24,}"),
)


def main() -> int:
    staged = "--staged" in sys.argv
    command = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"]
    if not staged:
        command = ["git", "ls-files", "-z"]
    result = subprocess.run(command, check=True, capture_output=True)
    failed = False
    for name in result.stdout.split(b"\0"):
        if not name:
            continue
        path = Path(name.decode("utf-8"))
        if path.name == ".pypirc" or (path.name.startswith(".env") and path.name != ".env.example"):
            print(f"Refusing credential file: {path}", file=sys.stderr)
            failed = True
            continue
        if staged:
            data = subprocess.run(
                ["git", "show", f":{path.as_posix()}"], check=True, capture_output=True
            ).stdout
        elif path.is_file():
            data = path.read_bytes()
        else:
            continue
        if any(pattern.search(data) for pattern in PATTERNS):
            print(f"Likely secret found: {path} (value withheld)", file=sys.stderr)
            failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
