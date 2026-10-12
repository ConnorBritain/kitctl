import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import kitctl
from kitctl.versioning import npm_dist_tag, pep440_to_semver, semver_to_pep440

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("pep440", "semver", "tag"),
    [
        ("0.1.0", "0.1.0", "latest"),
        ("0.1.0rc1", "0.1.0-rc.1", "next"),
        ("0.1.0rc12", "0.1.0-rc.12", "next"),
        ("1.2.3a4", "1.2.3-alpha.4", "next"),
        ("1.2.3b5", "1.2.3-beta.5", "next"),
        ("0.1.0.dev0", "0.1.0-0.dev.0", "next"),
    ],
)
def test_round_trip(pep440: str, semver: str, tag: str) -> None:
    assert pep440_to_semver(pep440) == semver
    assert semver_to_pep440(semver) == pep440
    assert npm_dist_tag(pep440) == tag


@pytest.mark.parametrize("bad", ["0.1", "0.1.0.post1", "0.1.0rc1.dev1", "v0.1.0"])
def test_rejects_unsupported(bad: str) -> None:
    with pytest.raises(ValueError):
        pep440_to_semver(bad)


def test_version_has_one_source() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert "version" not in project and "version" in project["dynamic"]
    launcher = (ROOT / "npm/bin/kitctl.mjs").read_text()
    assert not re.search(r"kitctl-\d", launcher)
    package = json.loads((ROOT / "npm/package.json").read_text())
    assert package["version"] == pep440_to_semver(kitctl.__version__)


def test_check_script_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/version.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
