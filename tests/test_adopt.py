import json
import tomllib
from pathlib import Path

import pytest

from kitctl import adopt
from kitctl.cli import main
from kitctl.hashing import file_digest
from kitctl.inventory import inventory
from kitctl.validate import validate
from tests.conftest import CANARY


@pytest.fixture
def warehouse(harnesses: Path, tmp_path: Path) -> Path:
    from kitctl import fs

    root = tmp_path / "home/kits"
    root.mkdir()
    fs.allow(root)  # the CLI does this via warehouse.resolve(allow_writes=True)
    return root


def test_adopt_skill_dir_with_provenance(warehouse: Path, tmp_path: Path) -> None:
    skill = tmp_path / "home/.claude/skills/writer"
    (skill / "node_modules/dep").mkdir(parents=True)
    (skill / "node_modules/dep/x.js").write_text("vendored")
    (skill / "notes.md").write_text("asset")
    before = file_digest(skill / "SKILL.md")
    assert main(["adopt", str(skill), "--to", "primitives"]) == 0
    target = warehouse / "primitives/skills/writer"
    meta = tomllib.loads((target / "primitive.toml").read_text())
    assert meta["kind"] == "skill" and meta["source"]["origin"] == str(skill)
    assert meta["content_hash"] == meta["source"]["original_hash"]
    assert (target / "files/SKILL.md").is_file() and (target / "files/notes.md").is_file()
    assert not (target / "files/node_modules").exists()
    assert file_digest(skill / "SKILL.md") == before and (skill / "notes.md").exists()
    assert validate(warehouse)["findings"] == []
    result = adopt.adopt_path(skill, warehouse, "primitives")
    assert result["status"] == "already adopted"


def test_adopt_collision_and_agent_file(warehouse: Path, tmp_path: Path) -> None:
    agent = tmp_path / "home/.claude/agents/reviewer.md"
    assert adopt.adopt_path(agent, warehouse)["status"] == "adopted"
    other = tmp_path / "elsewhere/agents/reviewer.md"
    other.parent.mkdir(parents=True)
    other.write_text("different")
    with pytest.raises(adopt.AdoptError, match="--name"):
        adopt.adopt_path(other, warehouse)
    assert adopt.adopt_path(other, warehouse, force=True)["status"] == "adopted"
    assert (warehouse / "inbox/agents/reviewer/files/reviewer.md").read_text() == "different"


def test_adopt_mcp_strips_credentials(warehouse: Path, harnesses: Path, capsys) -> None:
    assert main(["adopt", "mcp:github", "--harness", "claude", "--scope", "user"]) == 0
    out = capsys.readouterr().out
    assert "${GITHUB_TOKEN}" in out and "${TOKEN}" in out
    stored = (warehouse / "inbox/mcp/github/server.json").read_text()
    assert CANARY not in stored
    server = json.loads(stored)["github"]
    assert server["env"] == {"GITHUB_TOKEN": "${GITHUB_TOKEN}"}
    assert server["args"] == ["--token", "${TOKEN}"]
    assert main(["adopt", "mcp:sentry"]) == 0
    sentry = json.loads((warehouse / "inbox/mcp/sentry/server.json").read_text())["sentry"]
    assert sentry["url"] == "https://mcp.sentry.dev/mcp"
    assert sentry["headers"] == {"Authorization": "${AUTHORIZATION}"}
    assert validate(warehouse)["findings"] == []


def test_adopt_mcp_ambiguity_and_secret_refusal(warehouse: Path, harnesses: Path) -> None:
    entries = inventory([harnesses])["entries"]
    with pytest.raises(adopt.AdoptError, match="differing definitions"):
        adopt.adopt_mcp("github", entries, warehouse)
    secret = "sk-ant-" + "api03-" + "Z" * 30
    with pytest.raises(adopt.AdoptError, match="likely secret"):
        adopt.sanitize_server({"command": "x", "args": [secret]})


def test_adopt_requires_warehouse(harnesses: Path, tmp_path: Path) -> None:
    assert main(["adopt", str(tmp_path / "home/.claude/agents/reviewer.md")]) == 2
