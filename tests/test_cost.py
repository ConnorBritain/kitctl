"""Always-on listing cost in doctor, and the loadout budget that holds it."""

import json
from pathlib import Path

import pytest

from kitctl.cli import main
from kitctl.cost import METHOD, estimate_tokens, listing_cost, listing_line, strictest_budget
from kitctl.doctor import doctor
from kitctl.model import ModelError, parse_loadout
from kitctl.resolve import effective, resolve

INSTRUCTIONS = "Work in small steps."
SHOWN = b"---\nname: shown\ndescription: Shows the plan before acting.\n---\nBody.\n"
HIDDEN = (
    b"---\nname: hidden\ndescription: Runs only by name.\ndisable-model-invocation: true\n"
    b"---\nBody.\n"
)
AGENT = b"---\nname: reviewer\ndescription: Reviews diffs.\n---\nReview.\n"
CODEX_AGENT = (
    b'name = "reviewer"\ndescription = "Reviews diffs for Codex."\n'
    b'developer_instructions = "Review."\n'
)


def write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def no_tools(command: list[str]) -> tuple[int, str]:
    return 0, ""


@pytest.fixture
def warehouse(tmp_path: Path) -> Path:
    root = tmp_path / "kits"
    p = root / "primitives"
    write(
        p / "skills/shown.yaml",
        b"schema_version: 2\nkind: skill\nname: shown\ndescription: Old words.\n",
    )
    write(p / "skills/shown/SKILL.md", SHOWN)
    write(p / "skills/hidden.yaml", b"schema_version: 2\nkind: skill\nname: hidden\n")
    write(p / "skills/hidden/SKILL.md", HIDDEN)
    write(
        p / "skills/plain.yaml",
        b"schema_version: 2\nkind: skill\nname: plain\ndescription: No folder; listed as is.\n"
        b"external: true\n",
    )
    write(
        p / "agents/reviewer.yaml",
        b"schema_version: 2\nkind: agent\nname: reviewer\ndescription: Reviews.\n"
        b"harnesses: [claude, codex]\n",
    )
    write(p / "agents/reviewer/claude.md", AGENT)
    write(p / "agents/reviewer/codex.toml", CODEX_AGENT)
    write(
        p / "plugins/tools.yaml",
        b"schema_version: 2\nkind: plugin\nname: tools\ndescription: Plugin.\nexternal: true\n"
        b"skills: [lint, fmt, secret]\nharnesses:\n  claude: { id: tools@market }\n"
        b"  codex: { id: tools@market }\n",
    )
    write(
        p / "skills/tools-secret.yaml",
        b"schema_version: 2\nkind: skill\nname: tools-secret\nexternal: true\n"
        b"plugin: plugin:tools\nharnesses:\n  claude: { skill: 'tools:secret' }\n",
    )
    write(
        p / "mcp/docs.yaml",
        b"schema_version: 2\nkind: mcp\nname: docs\ntransport: http\nurl: https://docs.example\n",
    )
    write(
        root / "loadouts/base.yaml",
        b"schema_version: 2\nname: base\nskills: [skill:shown, skill:hidden, skill:plain]\n"
        b"agents: [agent:reviewer]\nmcp:\n  docs: { primitive: mcp:docs, enabled_tools: [a, b] }\n"
        + f"instructions: {INSTRUCTIONS}\n".encode()
        + b"budget: { tokens: 1000, skills: 1 }\n"
        b"profiles:\n  roomy:\n    budget: { skills: 5 }\n",
    )
    write(
        root / "loadouts/child.yaml",
        b"schema_version: 2\nname: child\nextends: [base]\nplugins: [plugin:tools]\n"
        b"disable: [skill:plain, skill:tools-secret]\nbudget: { skills: 4 }\n",
    )
    write(root / "loadouts/extra.yaml", b"schema_version: 2\nname: extra\ninstructions: More.\n")
    return root


def tokens(block: str, *lines: tuple[str, str]) -> int:
    return estimate_tokens(block) + sum(
        estimate_tokens(listing_line(name, text)) for name, text in lines
    )


def block(warehouse: Path, target: str, harness: str) -> str:
    text = resolve(warehouse, target, harness)["instructions"]["text"]
    assert isinstance(text, str) and INSTRUCTIONS in text
    return text


def test_estimate_is_ceil_chars_over_four() -> None:
    assert [estimate_tokens(t) for t in ("", "abcd", "abcde", "é" * 8)] == [0, 1, 2, 2]
    assert METHOD == "estimate: ceil(chars / 4)"


def test_hidden_skills_cost_nothing_and_skill_md_wins(warehouse: Path) -> None:
    cost = listing_cost(warehouse, [("base", "project")], "claude")
    assert cost["skills"] == 2  # shown and plain; hidden is not listed
    assert cost["tokens"] == tokens(
        INSTRUCTIONS,
        ("shown", "Shows the plan before acting."),
        ("plain", "No folder; listed as is."),
        ("reviewer", "Reviews diffs."),
    )
    assert any("1 MCP servers, 2 named enabled tools" in n for n in cost["not_measured"])
    codex = listing_cost(warehouse, [("base", "project")], "codex")
    assert codex["tokens"] == tokens(
        INSTRUCTIONS,
        ("shown", "Shows the plan before acting."),
        ("plain", "No folder; listed as is."),
        ("reviewer", "Reviews diffs for Codex."),
    )
    # Cursor's block is the whole rule file; the agent is a gap there, so it is not listed.
    cursor = listing_cost(warehouse, [("base", "project")], "cursor")
    assert cursor["tokens"] == tokens(
        block(warehouse, "base", "cursor"),
        ("shown", "Shows the plan before acting."),
        ("plain", "No folder; listed as is."),
    )


def test_disabled_and_plugin_skills(warehouse: Path, tmp_path: Path) -> None:
    plugin = tmp_path / "installed/tools"
    write(plugin / "skills/lint/SKILL.md", b"---\nname: lint\ndescription: Lints.\n---\n")
    write(plugin / "skills/fmt/SKILL.md", b"---\nname: fmt\ndisable-model-invocation: true\n---\n")
    write(plugin / "skills/secret/SKILL.md", b"---\nname: secret\ndescription: Disabled.\n---\n")
    installed = {"tools@market": str(plugin)}
    ids = {"plugin:tools": "tools@market"}
    cost = listing_cost(warehouse, [("child", "project")], "claude", installed, ids)
    # plain is disabled by child; fmt is hidden; secret is disabled by its native name.
    assert cost["skills"] == 2
    assert cost["tokens"] == tokens(
        block(warehouse, "child", "claude"),  # with the soft rules for the disables
        ("shown", "Shows the plan before acting."),
        ("lint", "Lints."),
        ("reviewer", "Reviews diffs."),
    )
    missing = listing_cost(warehouse, [("child", "project")], "claude", {}, ids)
    assert (
        "plugin:tools skills: not installed in Claude Code, declares 3" in missing["not_measured"]
    )
    codex = listing_cost(warehouse, [("child", "project")], "codex", installed, ids)
    assert any("Kit cannot see codex's installed plugins" in n for n in codex["not_measured"])


def test_budget_merges_child_and_profile_over_parent(warehouse: Path) -> None:
    assert effective(warehouse, "base").budget == {"tokens": (1000, "base"), "skills": (1, "base")}
    assert effective(warehouse, "child").budget == {
        "tokens": (1000, "base"),
        "skills": (4, "child"),
    }
    assert effective(warehouse, "base:roomy").budget["skills"] == (5, "base:roomy")
    assert strictest_budget(warehouse, ["child", "base:roomy"]) == {
        "tokens": (1000, "base"),
        "skills": (4, "child"),
    }
    # Budget is a loadout setting, not an effect: the resolved plan does not change.
    assert "budget" not in resolve(warehouse, "base", "claude")


def test_doctor_warns_over_budget_and_keeps_its_exit_code(
    warehouse: Path, sandbox: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = doctor(warehouse, [sandbox], "base", ("claude",), runner=no_tools)
    section = report["harnesses"]["claude"]["listing_cost"]
    assert section["method"] == METHOD and section["skills"] == 2
    budget = [f for f in report["findings"] if f["check"] == "budget"]
    assert [(f["severity"], f["subject"]) for f in budget] == [("warning", "claude")]
    assert "2 listed skills is over the budget of 1 (from base) by 1" in budget[0]["message"]
    roomy = doctor(warehouse, [sandbox], "base:roomy", ("claude",), runner=no_tools)
    assert not [f for f in roomy["findings"] if f["check"] == "budget"]
    args = ["doctor", "--loadout", "base", "--warehouse", str(warehouse), "--root", str(sandbox)]
    assert main([*args, "--harness", "claude"]) == 0
    out = capsys.readouterr().out
    assert "listing  claude: " in out and "warning  budget: claude" in out


def test_doctor_counts_the_repos_kit_yaml_loadouts(warehouse: Path, sandbox: Path) -> None:
    alone = doctor(warehouse, [sandbox], "base", ("claude",), runner=no_tools)
    write(
        sandbox / "kit.yaml", b"schema_version: 1\nloadouts: [extra, base]\nharnesses: [claude]\n"
    )
    both = doctor(warehouse, [sandbox], "base", ("claude",), runner=no_tools)
    more = both["harnesses"]["claude"]["listing_cost"]["tokens"]
    assert more == alone["harnesses"]["claude"]["listing_cost"]["tokens"] + estimate_tokens("More.")
    # Without --loadout, a Kit-managed repo still gets its listing cost (and nothing else).
    managed = doctor(warehouse, [sandbox], None, ("claude", "codex"), runner=no_tools)
    assert managed["harnesses"]["claude"]["listing_cost"]["tokens"] == more
    assert "codex" not in managed["harnesses"]  # kit.yaml names only claude
    assert [f["check"] for f in managed["findings"]] == ["budget"]


def test_doctor_reads_installed_plugin_skills_from_home(warehouse: Path, sandbox: Path) -> None:
    home = Path.home()
    plugin = home / ".claude/plugins/cache/tools"
    write(plugin / "skills/lint/SKILL.md", b"---\nname: lint\ndescription: Lints.\n---\n")
    write(
        home / ".claude/plugins/installed_plugins.json",
        json.dumps(
            {
                "version": 2,
                "plugins": {"tools@market": [{"scope": "user", "installPath": str(plugin)}]},
            }
        ).encode(),
    )
    without = doctor(warehouse, [sandbox], "child", ("claude",), runner=no_tools)
    notes = without["harnesses"]["claude"]["listing_cost"]["not_measured"]
    assert any("--root ~" in n for n in notes)
    report = doctor(warehouse, [home, sandbox], "child", ("claude",), runner=no_tools)
    cost = report["harnesses"]["claude"]["listing_cost"]
    assert cost["skills"] == 2 and not any("plugin:tools" in n for n in cost["not_measured"])


@pytest.mark.parametrize(
    "budget",
    ["{ tokens: true }", "{ tokens: 0 }", "{ skills: -1 }", "{ tokens: '9' }", "{ words: 1 }", "7"],
)
def test_invalid_budget_is_rejected(warehouse: Path, budget: str) -> None:
    write(
        warehouse / "loadouts/bad.yaml",
        f"schema_version: 2\nname: bad\nbudget: {budget}\n".encode(),
    )
    with pytest.raises(ModelError):
        parse_loadout(warehouse, "bad")
    write(
        warehouse / "loadouts/bad.yaml",
        f"schema_version: 2\nname: bad\nprofiles:\n  p:\n    budget: {budget}\n".encode(),
    )
    with pytest.raises(ModelError):
        parse_loadout(warehouse, "bad")
