from pathlib import Path

import pytest

from kitctl.cli import main
from kitctl.validate import validate


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def warehouse(tmp_path: Path) -> Path:
    root = tmp_path / "home/kits"
    write(
        root / "primitives/skills/writer/primitive.toml",
        'schema_version = 1\nkind = "skill"\nname = "writer"\nharnesses = ["claude", "codex"]\n'
        '[source]\norigin = "adopted"\npath = "skills/writer"\n',
    )
    write(
        root / "primitives/mcp/github/primitive.toml",
        'schema_version = 1\nkind = "mcp"\nname = "github"\n',
    )
    write(root / "loadouts/core.toml", 'schema_version = 1\nname = "core"\nskills = ["writer"]\n')
    write(
        root / "loadouts/web.toml",
        'schema_version = 1\nname = "web"\nextends = ["core"]\nmcp = ["mcp:github"]\n',
    )
    return root


def codes(root: Path) -> list[str]:
    return sorted(f["code"] for f in validate(root)["findings"])


def test_valid_warehouse(warehouse: Path) -> None:
    result = validate(warehouse)
    assert result["findings"] == [] and result["primitives"] == 2 and result["loadouts"] == 2
    assert main(["validate", "--warehouse", str(warehouse)]) == 0


def test_unknown_field_and_bad_slug(warehouse: Path) -> None:
    write(
        warehouse / "loadouts/Bad Name.toml", 'schema_version = 1\nname = "Bad Name"\nextra = 1\n'
    )
    assert codes(warehouse) == ["unknown-field", "unsafe-name"]


def test_missing_reference_and_traversal(warehouse: Path) -> None:
    write(
        warehouse / "loadouts/x.toml",
        'schema_version = 1\nname = "x"\nskills = ["ghost", "../etc"]\n',
    )
    write(
        warehouse / "primitives/skills/evil/primitive.toml",
        'schema_version = 1\nkind = "skill"\nname = "evil"\n[source]\npath = "../../outside"\n',
    )
    assert codes(warehouse) == ["missing-reference", "path-traversal", "path-traversal"]


def test_cycle(warehouse: Path) -> None:
    write(
        warehouse / "loadouts/core.toml", 'schema_version = 1\nname = "core"\nextends = ["web"]\n'
    )
    assert "cycle" in codes(warehouse)
    assert main(["validate", "--warehouse", str(warehouse)]) == 1


def test_missing_warehouse(tmp_path: Path) -> None:
    assert main(["validate", "--warehouse", str(tmp_path / "nope")]) == 2


def test_empty_warehouse_message(tmp_path: Path, capsys) -> None:
    (tmp_path / "home/kits").mkdir(parents=True)
    assert main(["validate"]) == 0
    assert "Nothing to validate" in capsys.readouterr().out
