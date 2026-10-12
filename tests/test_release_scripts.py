import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def test_release_notes_section() -> None:
    notes = load("release_notes")
    text = (
        "# Changelog\n\n## 0.2.0 — later\n\n- b\n\n"
        "## 0.1.0rc1 — 2026-10-12\n\n- a\n- c\n\n## 0.0.1\n- old\n"
    )
    assert notes.section(text, "0.1.0rc1") == "- a\n- c\n"
    assert notes.section(text, "0.1.0") is None


def test_version_github_output(tmp_path: Path) -> None:
    output = tmp_path / "out"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/version.py"), "--github-output"],
        env={"GITHUB_OUTPUT": str(output), "PATH": ""},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert set(values) == {"pep440", "semver", "npm_tag", "prerelease"}
