"""User scope: the same plan/apply/off machinery with home as the root (docs/apply.md)."""

from pathlib import Path

import pytest

from kitctl import engine
from kitctl.cli import main

WAREHOUSE = Path(__file__).parent / "fixtures" / "warehouse"
SETTINGS = b'{\n  "theme": "dark"\n}\n'
CODEX = b'# mine\nmodel = "gpt-5"\n\n[[skills.config]]\nname = "mine"\nenabled = false\n'


def tree(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.fixture
def home() -> Path:
    """The conftest's temporary HOME, with config the user wrote themselves."""
    root = Path.home()
    (root / ".claude").mkdir(parents=True, exist_ok=True)
    (root / ".codex").mkdir(parents=True, exist_ok=True)
    (root / ".claude" / "settings.json").write_bytes(SETTINGS)
    (root / ".codex" / "config.toml").write_bytes(CODEX)
    return root.resolve()


def run(command: str, *extra: str) -> int:
    return main([command, "brand-studio", "--user", "--warehouse", str(WAREHOUSE), *extra])


def test_use_writes_user_config_and_off_restores_home_byte_for_byte(home: Path) -> None:
    baseline = tree(home)
    assert run("use", "--yes") == 0
    settings = (home / ".claude" / "settings.json").read_text()
    assert '"theme": "dark"' in settings and "brand-builder@brand-builder" in settings
    codex = (home / ".codex" / "config.toml").read_text()
    assert codex.startswith(CODEX.decode()) and 'name = "ui-ux-pro-max"' in codex
    assert "kit:begin brand-studio" in (home / ".claude" / "CLAUDE.md").read_text()
    assert "kit:begin brand-studio" in (home / ".codex" / "AGENTS.md").read_text()
    assert (home / ".kit" / "kit.yaml").is_file() and (home / ".kit" / "kit.lock").is_file()
    for loose in ("kit.yaml", "kit.lock", "CLAUDE.md", "AGENTS.md", ".mcp.json", ".claude.json"):
        assert not (home / loose).exists(), loose
    assert all(e["state"] == "ok" for e in engine.status(home, WAREHOUSE)["entries"])
    assert run("off", "--yes") == 0
    assert tree(home) == baseline  # the user's own [[skills.config]] entry survives


def test_claude_mcp_is_a_remedy_not_a_write(home: Path) -> None:
    intent = engine.with_loadout(engine.read_intent(home), "brand-studio")
    plan = engine.make_plan(home, WAREHOUSE, intent)
    remedies = [g["remedy"] for g in plan.gaps if g["capability"] == "mcp_user_file"]
    assert any(r.startswith("claude mcp add --scope user chrome-devtools -- npx") for r in remedies)
    assert not any(t.endswith(".claude.json") or t.endswith(".mcp.json") for t in plan.changes)


def test_claude_config_dir_moves_the_targets_within_home(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / "alt-claude"))
    assert run("use", "--yes") == 0
    assert "brand-builder@brand-builder" in (home / "alt-claude" / "settings.json").read_text()
    assert (home / "alt-claude" / "CLAUDE.md").is_file()
    assert (home / ".claude" / "settings.json").read_bytes() == SETTINGS


def test_a_config_dir_outside_home_is_a_conflict(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "elsewhere"))
    intent = engine.with_loadout(engine.read_intent(home), "brand-studio")
    plan = engine.make_plan(home, WAREHOUSE, intent)
    assert any("CODEX_HOME" in c and "outside home" in c for c in plan.conflicts)


def test_user_and_repo_together_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["--user", "--repo", str(tmp_path), "--warehouse", str(WAREHOUSE)]
    assert main(["plan", *args]) == 2
    assert "pass one" in capsys.readouterr().err


def test_setup_steps_are_listed_not_run_and_the_user_is_warned(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("use", "--yes") == 0
    captured = capsys.readouterr()
    assert "setup steps run in projects" in captured.out
    assert "warning: user scope" in captured.err


def test_cursor_has_no_user_rule_file(home: Path) -> None:
    intent = {"schema_version": 1, "loadouts": ["brand-studio"], "harnesses": ["cursor"]}
    plan = engine.make_plan(home, WAREHOUSE, intent)
    assert not any(".cursor/rules" in t for t in plan.changes)
    assert any(g["capability"] == "instruction_file" for g in plan.gaps)


def test_a_comment_inside_a_kit_table_blocks_off_instead_of_vanishing(home: Path) -> None:
    assert run("use", "--yes") == 0
    config = home / ".codex" / "config.toml"
    text = config.read_text()
    marker = 'name = "ui-ux-pro-max"\nenabled = false\n'
    assert marker in text
    config.write_text(text.replace(marker, marker + "# my precious comment\n"))
    assert run("off", "--yes") == 1
    assert "# my precious comment" in config.read_text()


def test_a_split_array_of_tables_is_a_conflict_not_a_crash(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (home / ".codex" / "config.toml").write_bytes(
        b'[[skills.config]]\nname = "a"\nenabled = false\n\n[tui]\nx = 1\n\n'
        b'[[skills.config]]\nname = "b"\nenabled = false\n'
    )
    assert run("use", "--yes") == 1
    assert "kitctl:" in capsys.readouterr().err


def test_a_bad_config_dir_does_not_block_off(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = tree(home)
    assert run("use", "--yes") == 0
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "elsewhere"))
    assert run("off", "--yes") == 0
    assert tree(home) == baseline


def test_other_files_in_dot_kit_survive(home: Path) -> None:
    (home / ".kit").mkdir()
    (home / ".kit" / "other-tool.db").write_bytes(b"theirs")
    baseline = tree(home)
    assert run("use", "--yes") == 0
    assert run("off", "--yes") == 0
    assert tree(home) == baseline


def test_config_dir_cannot_be_home_or_kits_state(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    intent = engine.with_loadout(engine.read_intent(home), "brand-studio")
    assert any("not home" in c for c in engine.make_plan(home, WAREHOUSE, intent).conflicts)
