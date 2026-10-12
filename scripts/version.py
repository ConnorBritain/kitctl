"""Print or check the single-sourced version (src/kitctl/__init__.py) and its npm form.

python scripts/version.py            # PEP 440, semver, dist-tag
python scripts/version.py --check    # fail if any copy disagrees
"""

from __future__ import annotations

import json
import os
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kitctl.versioning import npm_dist_tag, pep440_to_semver  # noqa: E402


def read_version() -> str:
    text = (ROOT / "src/kitctl/__init__.py").read_text("utf-8")
    match = re.search(r'^__version__ = "([^"]+)"$', text, re.MULTILINE)
    if not match:
        raise SystemExit("__version__ not found in src/kitctl/__init__.py")
    return match.group(1)


def problems(version: str) -> list[str]:
    found = []
    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    if "version" in project or "version" not in project.get("dynamic", []):
        found.append("pyproject.toml must declare version as dynamic, not a literal")
    package = json.loads((ROOT / "npm/package.json").read_text("utf-8"))
    if package["version"] != pep440_to_semver(version):
        found.append(
            f"npm/package.json version {package['version']} != {pep440_to_semver(version)}"
        )
    launcher = (ROOT / "npm/bin/kitctl.mjs").read_text("utf-8")
    if re.search(r"kitctl-\d", launcher):
        found.append("npm/bin/kitctl.mjs hardcodes a wheel filename")
    ref = os.environ.get("GITHUB_REF", "")
    if ref.startswith("refs/tags/v") and ref.removeprefix("refs/tags/v") != version:
        found.append(f"tag {ref} does not match version {version}")
    return found


def main() -> int:
    version = read_version()
    if "--check" in sys.argv:
        issues = problems(version)
        for issue in issues:
            print(f"version mismatch: {issue}", file=sys.stderr)
        if not issues:
            print(f"version {version} consistent")
        return int(bool(issues))
    values = {
        "pep440": version,
        "semver": pep440_to_semver(version),
        "npm_tag": npm_dist_tag(version),
        "prerelease": str(npm_dist_tag(version) == "next").lower(),
    }
    if "--github-output" in sys.argv:
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
            handle.writelines(f"{key}={value}\n" for key, value in values.items())
    print(" ".join(f"{key}={value}" for key, value in values.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
