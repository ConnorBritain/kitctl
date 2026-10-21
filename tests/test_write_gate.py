"""Writes in a real repository: anywhere, after the plan and a confirmation; git state warns."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from kitctl import engine
from kitctl.cli import main

WAREHOUSE = Path(__file__).parent / "fixtures" / "warehouse"
INTENT = "schema_version: 1\nloadouts: []\nharnesses:\n  - claude\n  - codex\n"


def clean_env() -> dict[str, str]:
    """No inherited GIT_DIR and friends: under a hook they would aim at the developer's repo."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Owner",
            "-c",
            "user.email=owner@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.autocrlf=false",
            *args,
        ],
        capture_output=True,
        text=True,
        check=True,
        env=clean_env(),
    )
    return done.stdout.strip()


def tree(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and not ({".git", ".kit"} & set(p.relative_to(root).parts))
    }


@pytest.fixture
def real_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repository outside any sandbox, committed on a feature branch."""
    monkeypatch.delenv(engine.SANDBOX_ENV, raising=False)
    repo = tmp_path / "project"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "CLAUDE.md").write_bytes(b"# Project\n")
    (repo / engine.INTENT).write_bytes(INTENT.encode())
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "start")
    git(repo, "checkout", "-q", "-b", "feature")
    return repo.resolve()


def plan_for(repo: Path, target: str = "brand-studio") -> engine.Plan:
    return engine.make_plan(repo, WAREHOUSE, engine.with_loadout(engine.read_intent(repo), target))


def off_plan(repo: Path, name: str = "brand-studio") -> engine.Plan:
    return engine.make_plan(repo, WAREHOUSE, engine.without_loadout(engine.read_intent(repo), name))


def test_writes_in_a_real_repo_and_off_restores_the_bytes(real_repo: Path) -> None:
    baseline = tree(real_repo)
    assert "CLAUDE.md" in engine.apply_plan(plan_for(real_repo))
    engine.apply_plan(off_plan(real_repo))
    assert tree(real_repo) == baseline


def test_no_git_still_writes_and_off_is_the_undo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(engine.SANDBOX_ENV, raising=False)
    repo = tmp_path / "plain"
    repo.mkdir()
    (repo / "CLAUDE.md").write_bytes(b"# Mine\n")
    baseline = tree(repo)
    assert any("not a git repository" in w for w in engine.write_warnings(repo))
    engine.apply_plan(plan_for(repo))
    engine.apply_plan(off_plan(repo))
    assert tree(repo) == baseline


def test_git_state_warns_and_never_blocks(real_repo: Path) -> None:
    assert engine.write_warnings(real_repo) == []
    (real_repo / "notes.txt").write_text("untracked\n")
    assert engine.write_warnings(real_repo) == []  # untracked files are not the owner's edits
    (real_repo / "CLAUDE.md").write_bytes(b"# Project\n\nunsaved\n")
    git(real_repo, "checkout", "-q", "main")
    warnings = engine.write_warnings(real_repo)
    assert any("uncommitted" in w for w in warnings) and any("on main" in w for w in warnings)
    assert engine.apply_plan(plan_for(real_repo))
    assert b"unsaved" in (real_repo / "CLAUDE.md").read_bytes()


def test_the_remote_default_branch_warns(real_repo: Path, tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "trunk", str(remote)], check=True, env=clean_env()
    )
    git(real_repo, "branch", "-q", "-m", "feature", "trunk")
    git(real_repo, "remote", "add", "origin", str(remote))
    git(real_repo, "push", "-q", "origin", "trunk")
    git(real_repo, "remote", "set-head", "origin", "trunk")
    assert any("on trunk" in w for w in engine.write_warnings(real_repo))


def test_inherited_git_dir_does_not_answer_for_the_repo(
    real_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q", "-b", "main")
    (other / "a.txt").write_text("a\n")
    git(other, "add", "-A")
    git(other, "commit", "-q", "-m", "a")
    monkeypatch.setenv("GIT_DIR", str(real_repo / ".git"))  # as inside a git hook
    assert any("on main" in w for w in engine.write_warnings(other.resolve()))


def test_the_sandbox_still_confines_writes_when_set(
    real_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv(engine.SANDBOX_ENV, str(elsewhere))
    with pytest.raises(engine.EngineError, match="outside"):
        engine.apply_plan(plan_for(real_repo))
    assert not (real_repo / "kit.lock").exists()


def test_an_old_live_opt_in_still_reads(real_repo: Path) -> None:
    (real_repo / engine.INTENT).write_bytes(
        INTENT.replace("loadouts", "live: true\nloadouts").encode()
    )
    assert engine.read_intent(real_repo)["loadouts"] == []
    engine.apply_plan(plan_for(real_repo))
    assert b"live: true" in (real_repo / engine.INTENT).read_bytes()


def test_crlf_kit_yaml_round_trips(real_repo: Path) -> None:
    (real_repo / engine.INTENT).write_bytes(INTENT.replace("\n", "\r\n").encode())
    baseline = tree(real_repo)
    engine.apply_plan(plan_for(real_repo))
    assert b"\r\n" in (real_repo / engine.INTENT).read_bytes()
    engine.apply_plan(off_plan(real_repo))
    assert tree(real_repo) == baseline


def test_cli_asks_before_writing(
    real_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["--repo", str(real_repo), "--warehouse", str(WAREHOUSE)]
    baseline = tree(real_repo)
    assert main(["use", "brand-studio", *args, "--dry-run"]) == 0
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main(["use", "brand-studio", *args]) == 1  # not a terminal and no --yes
    assert "re-run with --yes" in capsys.readouterr().err
    assert tree(real_repo) == baseline

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda: "n")
    assert main(["use", "brand-studio", *args]) == 1
    assert "[y/N]" in capsys.readouterr().err
    assert tree(real_repo) == baseline

    monkeypatch.setattr("builtins.input", lambda: "y")
    assert main(["use", "brand-studio", *args, "--json"]) == 0
    out = capsys.readouterr().out
    assert "[y/N]" not in out  # the prompt goes to stderr; --json stays parseable
    capsys.readouterr()
    assert main(["status", *args, "--json"]) == 0
    assert all(e["state"] == "ok" for e in json.loads(capsys.readouterr().out)["entries"])
    assert main(["off", "brand-studio", *args, "--yes"]) == 0
    assert tree(real_repo) == baseline


def test_cli_warns_and_accepts_the_old_live_flag(
    real_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    git(real_repo, "checkout", "-q", "main")
    args = ["--repo", str(real_repo), "--warehouse", str(WAREHOUSE)]
    assert main(["use", "brand-studio", *args, "--yes", "--live"]) == 0
    err = capsys.readouterr().err
    assert "--live is no longer needed" in err and "warning: on main" in err


def test_a_symlinked_state_directory_is_refused(real_repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (real_repo / ".kit").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(engine.EngineError, match="symlink"):
        engine.apply_plan(plan_for(real_repo))
    assert list(outside.iterdir()) == []
