"""Small defense-in-depth check; never print matching secret values."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kitctl.secretscan import find_bytes, is_config_name  # noqa: E402


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
        kinds = find_bytes(data, config_context=is_config_name(path.name))
        if kinds:
            print(
                f"Likely secret found: {path} ({', '.join(kinds)}; value withheld)", file=sys.stderr
            )
            failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
