import tomllib
from pathlib import Path

import pytest

from kitctl import config
from kitctl.cli import main
from kitctl.paths import resolve


def test_toml_round_trip_with_awkward_strings() -> None:
    settings = config.UserConfig(
        repo_parents=["C:\\Users\\me\\Code", 'odd "quoted" \u00e9 path', "tab\there"],
        warehouse="~/kits",
        discovery_depth=2,
    )
    text = config.dumps(settings)
    assert config.parse(text) == settings
    assert tomllib.loads(text)["repo_parents"][0] == "C:\\Users\\me\\Code"


@pytest.mark.parametrize(
    "text",
    [
        "bogus = 1\n",
        'discovery_depth = "3"\n',
        "discovery_depth = true\n",
        "schema_version = 9\n",
        "discovery_depth = 99\n",
        "repo_parents = [1]\n",
    ],
)
def test_rejects_invalid_config(text: str) -> None:
    with pytest.raises(config.ConfigError):
        config.parse(text)


def test_init_non_interactive_writes_only_config(tmp_path: Path) -> None:
    code = tmp_path / "home/Code"
    (code / "app/.git").mkdir(parents=True)
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    assert main(["init", "--non-interactive"]) == 0
    target = resolve().kit_config_file
    after = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    added = set(after) - set(before)
    assert {tmp_path / p for p in added if (tmp_path / p).is_file()} == {target}
    loaded = config.load(target)
    assert loaded is not None and loaded.repo_parents == [str(code)]
    assert loaded.warehouse == str(tmp_path / "home/kits")
    assert main(["init", "--non-interactive"]) == 2  # refuses to overwrite
    assert main(["init", "--non-interactive", "--force", "--depth", "1"]) == 0
    reloaded = config.load(target)
    assert reloaded is not None and reloaded.discovery_depth == 1


def test_init_interactive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter([str(tmp_path / "work"), str(tmp_path / "wh"), "2"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    (tmp_path / "work/x/.git").mkdir(parents=True)
    assert main(["init"]) == 0
    loaded = config.load(resolve().kit_config_file)
    assert loaded == config.UserConfig([str(tmp_path / "work")], str(tmp_path / "wh"), 2)
    assert loaded.nested_depth == 6 and loaded.include_worktrees is False


def test_inventory_uses_config_repos(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    for name in ("one", "two"):
        repo = tmp_path / "home/Code" / name
        (repo / ".git").mkdir(parents=True)
        (repo / "AGENTS.md").write_text(name)
    assert main(["init", "--non-interactive"]) == 0
    capsys.readouterr()
    assert main(["inventory"]) == 0
    assert "across 2 repos" in capsys.readouterr().out
