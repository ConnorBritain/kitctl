"""Live apply: writes outside $KITCTL_SANDBOX need --live and the owner's opt-in in the repo."""

import json
import subprocess
from pathlib import Path

import pytest

from kitctl import engine
from kitctl.cli import main

WAREHOUSE = Path(__file__).parent / "fixtures" / "warehouse"
LIVE_INTENT = "schema_version: 1\nlive: true\nloadouts: []\nharnesses:\n  - claude\n  - codex\n"


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
    )
    return done.stdout.strip()


def tree(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(root).parts
    }


@pytest.fixture
def live_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A real-looking repo outside any sandbox: live opted in, committed, on a feature branch."""
    monkeypatch.delenv(engine.SANDBOX_ENV, raising=False)
    repo = tmp_path / "project"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "CLAUDE.md").write_bytes(b"# Project\n")
    (repo / engine.INTENT).write_bytes(LIVE_INTENT.encode())
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "opt in to live apply")
    git(repo, "checkout", "-q", "-b", "feature")
    return repo.resolve()


def plan_for(repo: Path, target: str = "brand-studio") -> engine.Plan:
    return engine.make_plan(repo, WAREHOUSE, engine.with_loadout(engine.read_intent(repo), target))


def test_live_apply_with_every_opt_in_writes_and_off_restores(live_repo: Path) -> None:
    baseline = tree(live_repo)
    written = engine.apply_plan(plan_for(live_repo), live=True)
    assert "CLAUDE.md" in written and (live_repo / "kit.lock").exists()
    assert "live: true" in (live_repo / engine.INTENT).read_text()  # use keeps the opt-in
    git(live_repo, "add", "-A")
    git(live_repo, "commit", "-q", "-m", "kit: brand-studio")
    plan = engine.make_plan(
        live_repo, WAREHOUSE, engine.without_loadout(engine.read_intent(live_repo), "brand-studio")
    )
    engine.apply_plan(plan, live=True)
    after = tree(live_repo)
    assert {k: v for k, v in after.items() if not k.startswith(".kit/")} == baseline


def test_without_live_flag_refuses(live_repo: Path) -> None:
    with pytest.raises(engine.EngineError, match="pass --live"):
        engine.apply_plan(plan_for(live_repo))
    assert not (live_repo / "kit.lock").exists()


def test_uncommitted_opt_in_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(engine.SANDBOX_ENV, raising=False)
    repo = tmp_path / "project"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / engine.INTENT).write_text(LIVE_INTENT.replace("live: true\n", ""))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "no opt-in")
    git(repo, "checkout", "-q", "-b", "feature")
    with pytest.raises(engine.EngineError, match="does not say `live: true`"):
        engine.apply_plan(plan_for(repo), live=True)
    # Saying it in the working copy is not enough: the opt-in is what was committed.
    (repo / engine.INTENT).write_text(LIVE_INTENT)
    with pytest.raises(engine.EngineError, match="does not say `live: true`"):
        engine.apply_plan(plan_for(repo), live=True)
    assert not (repo / "kit.lock").exists()


def test_live_false_or_a_string_is_not_an_opt_in(live_repo: Path) -> None:
    (live_repo / engine.INTENT).write_text(LIVE_INTENT.replace("live: true", 'live: "true"'))
    git(live_repo, "commit", "-q", "-am", "quoted")
    with pytest.raises(engine.EngineError, match="live must be true or false"):
        engine.read_intent(live_repo)
    assert any("live: true" in r for r in engine.live_refusals(live_repo))


def test_dirty_tracked_tree_refuses(live_repo: Path) -> None:
    (live_repo / "CLAUDE.md").write_text("# Project\n\nunsaved work\n")
    with pytest.raises(engine.EngineError, match="uncommitted changes"):
        engine.apply_plan(plan_for(live_repo), live=True)
    assert (live_repo / "CLAUDE.md").read_text() == "# Project\n\nunsaved work\n"


def test_untracked_files_do_not_block(live_repo: Path) -> None:
    (live_repo / "notes.txt").write_text("scratch\n")
    assert engine.live_refusals(live_repo) == []


def test_default_branch_refuses(live_repo: Path) -> None:
    git(live_repo, "checkout", "-q", "main")
    with pytest.raises(engine.EngineError, match="main is a default branch"):
        engine.apply_plan(plan_for(live_repo), live=True)


def test_default_branch_comes_from_origin_head(live_repo: Path, tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "trunk", str(remote)], check=True)
    git(live_repo, "branch", "-q", "-m", "feature", "trunk")
    git(live_repo, "remote", "add", "origin", str(remote))
    git(live_repo, "push", "-q", "origin", "trunk")
    git(live_repo, "remote", "set-head", "origin", "trunk")
    assert engine.live_refusals(live_repo) == ["trunk is a default branch; apply on another branch"]
    git(live_repo, "checkout", "-q", "main")  # main stays protected whatever origin says
    assert engine.live_refusals(live_repo) == ["main is a default branch; apply on another branch"]


def test_detached_head_refuses(live_repo: Path) -> None:
    git(live_repo, "checkout", "-q", "--detach")
    with pytest.raises(engine.EngineError, match="HEAD is detached"):
        engine.apply_plan(plan_for(live_repo), live=True)


def test_not_a_git_repo_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(engine.SANDBOX_ENV, raising=False)
    repo = tmp_path / "plain"
    repo.mkdir()
    (repo / engine.INTENT).write_text(LIVE_INTENT)
    with pytest.raises(engine.EngineError, match="not a git work tree"):
        engine.apply_plan(plan_for(repo), live=True)


def test_every_reason_is_reported_at_once(live_repo: Path) -> None:
    git(live_repo, "checkout", "-q", "main")
    (live_repo / engine.INTENT).write_text(LIVE_INTENT.replace("live: true\n", ""))
    git(live_repo, "commit", "-q", "-am", "drop opt-in")
    (live_repo / "CLAUDE.md").write_text("dirty\n")
    reasons = engine.live_refusals(live_repo)
    assert len(reasons) == 3, reasons


def test_sandbox_outside_still_allows_a_live_opt_in(
    live_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv(engine.SANDBOX_ENV, str(elsewhere))
    with pytest.raises(engine.EngineError, match="outside"):
        engine.apply_plan(plan_for(live_repo))
    assert engine.apply_plan(plan_for(live_repo), live=True)


def test_interrupted_live_apply_recovers_on_its_own_dirty_files(
    live_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = plan_for(live_repo)
    original = engine._finish
    monkeypatch.setattr(engine, "_finish", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        engine.apply_plan(plan, live=True)
    monkeypatch.setattr(engine, "_finish", original)
    assert (live_repo / engine.JOURNAL).exists()
    # Only recovery may run on the journal's own uncommitted files; a fresh apply may not.
    assert any("uncommitted" in r for r in engine.live_refusals(live_repo))
    assert engine.live_refusals(live_repo, recovering=True) == []
    assert engine.recover(live_repo, live=True)
    assert any("uncommitted" in r for r in engine.live_refusals(live_repo, recovering=True))


def test_recovery_refuses_when_other_files_are_dirty(
    live_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (live_repo / "README.md").write_bytes(b"readme\n")
    git(live_repo, "add", "README.md")
    git(live_repo, "commit", "-q", "-m", "readme")
    original = engine._finish
    monkeypatch.setattr(engine, "_finish", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        engine.apply_plan(plan_for(live_repo), live=True)
    monkeypatch.setattr(engine, "_finish", original)
    (live_repo / "README.md").write_bytes(b"the owner's unsaved edit\n")
    with pytest.raises(engine.EngineError, match="uncommitted"):
        engine.recover(live_repo, live=True)


def test_inherited_git_dir_does_not_answer_for_the_repo(
    live_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q", "-b", "main")
    (other / engine.INTENT).write_bytes(b"schema_version: 1\n")
    git(other, "add", "-A")
    git(other, "commit", "-q", "-m", "no opt-in")
    monkeypatch.setenv("GIT_DIR", str(live_repo / ".git"))  # as inside a git hook
    reasons = engine.live_refusals(other.resolve())
    assert any("live: true" in r for r in reasons) and any("main" in r for r in reasons)


def test_crlf_kit_yaml_round_trips(live_repo: Path) -> None:
    (live_repo / engine.INTENT).write_bytes(LIVE_INTENT.replace("\n", "\r\n").encode())
    git(live_repo, "commit", "-q", "-am", "crlf")
    baseline = tree(live_repo)
    engine.apply_plan(plan_for(live_repo), live=True)
    assert b"\r\n" in (live_repo / engine.INTENT).read_bytes()
    git(live_repo, "add", "-A")
    git(live_repo, "commit", "-q", "-m", "kit: brand-studio")
    plan = engine.make_plan(
        live_repo, WAREHOUSE, engine.without_loadout(engine.read_intent(live_repo), "brand-studio")
    )
    engine.apply_plan(plan, live=True)
    after = tree(live_repo)
    assert {k: v for k, v in after.items() if not k.startswith(".kit/")} == baseline


def test_cli_live_flag(live_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = ["--repo", str(live_repo), "--warehouse", str(WAREHOUSE)]
    baseline = tree(live_repo)
    assert main(["use", "brand-studio", *args, "--dry-run"]) == 0  # a dry run needs no opt-in
    assert main(["use", "brand-studio", *args, "--yes"]) == 1
    assert "pass --live" in capsys.readouterr().err
    assert tree(live_repo) == baseline
    assert main(["use", "brand-studio", *args, "--yes", "--live"]) == 0
    capsys.readouterr()
    assert main(["status", *args, "--json"]) == 0
    assert all(e["state"] == "ok" for e in json.loads(capsys.readouterr().out)["entries"])
    assert main(["off", "brand-studio", *args, "--yes", "--live"]) == 1  # not committed yet
    assert "uncommitted changes" in capsys.readouterr().err
