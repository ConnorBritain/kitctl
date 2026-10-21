"""Setup steps: resolved in the project, run by `use` after a confirmation, never Kit-owned."""

import json
import sys
from pathlib import Path

import pytest

from kitctl import engine
from kitctl.cli import main


@pytest.fixture
def warehouse(tmp_path: Path) -> Path:
    root = tmp_path / "kits"
    (root / "loadouts").mkdir(parents=True)
    (root / "primitives").mkdir()
    script = "open('made.txt', 'w').write('made')"
    (root / "loadouts" / "maker.yaml").write_text(
        "schema_version: 2\nname: maker\ndescription: Makes a file.\nextends: []\n"
        "instructions: Uses maker.\n"
        f"setup:\n  - run: [{json.dumps(sys.executable)}, -c, {json.dumps(script)}]\n"
        "    when: file-exists:INPUT.md\n    outputs: [made.txt]\n"
    )
    return root


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(engine.SANDBOX_ENV, str(tmp_path))
    repo = tmp_path / "project"
    repo.mkdir()
    return repo.resolve()


def test_when_and_plugin_paths_resolve_or_say_why_not(project: Path, tmp_path: Path) -> None:
    home = tmp_path / "home"
    steps = [
        {"run": ["node", "${plugin:brand-builder}/scripts/x.mjs"], "harness": "any"},
        {"run": ["node", "${plugin:missing}/x.mjs"], "harness": "any"},
        {"run": ["echo"], "when": "file-exists:BRAND.md", "harness": "any"},
    ]
    installed = home / ".claude" / "plugins" / "installed_plugins.json"
    installed.parent.mkdir(parents=True)
    installed.write_text(
        json.dumps(
            {
                "version": 2,
                "plugins": {
                    "brand-builder@brand-builder": [
                        {"scope": "user", "installPath": "/opt/bb", "version": "0.3.1"}
                    ]
                },
            }
        )
    )
    runs = engine.setup_runs(project, steps, home)
    assert runs[0]["argv"] == ["node", "/opt/bb/scripts/x.mjs"]
    assert "missing is not installed" in runs[1]["skip"]
    assert runs[2]["skip"] == "BRAND.md not found"
    (project / "BRAND.md").write_text("# Brand\n")
    assert engine.setup_runs(project, steps[2:], home)[0]["argv"] == ["echo"]


def test_use_runs_setup_with_yes_and_off_leaves_its_outputs(
    project: Path, warehouse: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["--repo", str(project), "--warehouse", str(warehouse), "--yes"]
    assert main(["use", "maker", *args]) == 0
    assert "setup skipped (INPUT.md not found)" in capsys.readouterr().out
    assert not (project / "made.txt").exists()
    (project / "INPUT.md").write_text("in\n")
    assert main(["use", "maker", *args]) == 0  # again: config is current, setup runs now
    assert (project / "made.txt").read_text() == "made"
    assert main(["off", "maker", *args]) == 0
    assert (project / "made.txt").exists()  # setup outputs are the tool's, not Kit's


def test_setup_asks_separately_on_a_terminal(
    project: Path, warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (project / "INPUT.md").write_text("in\n")
    answers = iter(["y", "n"])
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda: next(answers))
    assert main(["use", "maker", "--repo", str(project), "--warehouse", str(warehouse)]) == 0
    assert (project / "CLAUDE.md").exists()  # the config was written
    assert not (project / "made.txt").exists()  # the setup step was declined


def test_a_failing_setup_step_fails_the_command(
    project: Path, warehouse: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (project / "INPUT.md").write_text("in\n")
    loadout = warehouse / "loadouts" / "maker.yaml"
    loadout.write_text(
        loadout.read_text().replace("open('made.txt', 'w').write('made')", "raise SystemExit(3)")
    )
    args = ["--repo", str(project), "--warehouse", str(warehouse), "--yes"]
    assert main(["use", "maker", *args]) == 1
    assert "setup step exited 3" in capsys.readouterr().err
