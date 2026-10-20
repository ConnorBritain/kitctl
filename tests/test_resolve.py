import json
import os
import shutil
from pathlib import Path

import pytest

from kitctl.cli import main
from kitctl.model import ModelError, content_hash, parse_loadout, parse_primitive
from kitctl.resolve import resolve
from kitctl.validate import validate

FIXTURES = Path(__file__).parent / "fixtures"
WAREHOUSE = FIXTURES / "warehouse"
CASES = FIXTURES / "cases"
EXPECTED = FIXTURES / "expected"
GOLDEN = [
    (target, harness)
    for target in ("brand-studio", "brand-studio:audit", "brand-studio:voice")
    for harness in ("claude", "codex", "cursor")
]


def codes(error: pytest.ExceptionInfo[ModelError]) -> set[str]:
    return {problem.code for problem in error.value.problems}


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def cases(tmp_path: Path) -> Path:
    copy = tmp_path / "cases"
    shutil.copytree(CASES, copy)
    return copy


@pytest.mark.parametrize(("target", "harness"), GOLDEN)
def test_brand_studio_matches_expected_json(target: str, harness: str) -> None:
    expected = EXPECTED / f"{target.replace(':', '.')}.{harness}.json"
    result = resolve(WAREHOUSE, target, harness)
    if os.environ.get("KITCTL_UPDATE_GOLDEN") == "1":
        expected.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    assert result == json.loads(expected.read_text(encoding="utf-8"))


def test_golden_warehouse_validates_cleanly() -> None:
    report = validate(WAREHOUSE)
    assert report["problems"] == []
    assert report["loadouts"] == 2
    assert report["resolutions"] == 3 * (1 + 2) + 3  # every loadout and profile, per harness


def test_brand_studio_compiles_the_proposal_table() -> None:
    claude = resolve(WAREHOUSE, "brand-studio", "claude")
    keys = {(e["target"], e["key"]): e["value"] for e in claude["effects"]}
    assert keys[(".claude/settings.json", "/enabledPlugins/brand-builder@brand-builder")] is True
    assert (
        keys[(".claude/settings.json", "/enabledPlugins/frontend-design@claude-plugins-official")]
        is False
    )
    assert keys[(".mcp.json", "/mcpServers/chrome-devtools")]["args"][:2] == [
        "-y",
        "chrome-devtools-mcp@1.11.0",
    ]
    assert "--no-usage-statistics" in keys[(".mcp.json", "/mcpServers/chrome-devtools")]["args"]
    assert claude["instructions"]["target"] == "CLAUDE.md"
    assert claude["setup"][0]["owned"] is False
    assert {g["capability"] for g in claude["gaps"]} == {"marketplace_sha_pin", "skill_disable"}
    codex = resolve(WAREHOUSE, "brand-studio", "codex")
    codex_keys = {(e["target"], e["key"]) for e in codex["effects"]}
    assert (".codex/config.toml", "mcp_servers.leonardo") in codex_keys
    assert codex["instructions"]["target"] == "AGENTS.md"
    severities = {(g["subject"], g["severity"]) for g in codex["gaps"]}
    assert ("plugin:brand-builder", "trust-required") in severities
    assert ("marketplace:brand-builder", "manual-step") in severities
    evidence = {e["key"]: e["evidence"] for e in codex["effects"]}
    assert evidence["marketplaces.brand-builder"] == "verified"  # Codex 0.163.0 sandbox probe
    assert evidence['plugins."hallmark@hallmark".enabled'] == "documented"
    disables = {d["identity"]: d["status"] for d in codex["disables"]}
    assert disables["plugin:anthropic/frontend-design"] == "not-applicable"
    assert "plugin_hooks" not in {g["capability"] for g in codex["gaps"]}
    assert {s["identity"] for s in codex["skipped"]} == {"hook:brand-builder-codex-edit"}


def test_fallback_hook_fills_slot_when_plugin_hooks_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kitctl import harness as facts

    codex = facts.CODEX
    patched = dict(codex.facts)
    patched["plugin_hooks"] = facts.Fact(False, "verified", "probe")
    monkeypatch.setitem(facts.HARNESS_FACTS, "codex", facts.Harness("codex", "x", "y", patched))
    result = resolve(WAREHOUSE, "brand-studio", "codex")
    hook = [e for e in result["effects"] if e["source"] == "hook:brand-builder-codex-edit"]
    assert [(e["target"], e["key"]) for e in hook] == [
        (".codex/hooks.json", "/hooks/PostToolUse/-")
    ]
    assert hook[0]["value"]["matcher"] == "apply_patch|Edit|Write"
    assert result["slots"]["edit.hook"]["provider"] == "hook:brand-builder-codex-edit"
    assert result["slots"]["edit.hook"]["status"] == "fallback"
    trust = {g["subject"] for g in result["gaps"] if g["severity"] == "trust-required"}
    assert "hook:brand-builder-codex-edit" in trust


def test_profile_audit_is_a_subset_and_appends_instructions() -> None:
    result = resolve(WAREHOUSE, "brand-studio:audit", "claude")
    servers = [e["key"] for e in result["effects"] if e["target"] == ".mcp.json"]
    assert servers == ["/mcpServers/chrome-devtools"]
    assert "Audit only" in result["instructions"]["text"]
    assert result["profile"] == "audit"


def test_profile_voice_leaves_browser_slot_unfilled() -> None:
    result = resolve(WAREHOUSE, "brand-studio:voice", "claude")
    assert result["slots"]["browser.mcp"]["status"] == "unfilled"
    assert [t["identity"] for t in result["tools"]["requires"]] == ["tool:node"]


def test_unknown_profile_is_an_error() -> None:
    with pytest.raises(ModelError) as error:
        resolve(WAREHOUSE, "brand-studio:nope", "claude")
    assert codes(error) == {"missing"}


def test_slot_conflict_names_both_sources(cases: Path) -> None:
    with pytest.raises(ModelError) as error:
        resolve(cases, "slot-conflict", "claude")
    message = " ".join(p.message for p in error.value.problems)
    assert "plugin:gen-a" in message and "plugin:gen-b" in message
    assert codes(error) == {"slot-conflict"}


def test_unmanaged_observed_provider_conflicts_with_slot(cases: Path) -> None:
    (cases / "loadouts/one-gen.yaml").write_text(
        "schema_version: 2\nname: one-gen\nplugins: [plugin:gen-a]\n"
        "slots: {design.generator: plugin:gen-a}\n"
    )
    assert resolve(cases, "one-gen", "claude")["slots"]["design.generator"]["status"] == "filled"
    with pytest.raises(ModelError) as error:
        resolve(cases, "one-gen", "claude", observed={"plugin:gen-b"})
    assert "observed (unmanaged)" in error.value.problems[0].message


def test_disable_overrides_outer_layer(cases: Path) -> None:
    result = resolve(cases, "child", "claude")
    assert result["layers"] == ["base", "child"]
    assert result["disables"] == [
        {"identity": "skill:helper", "layer": "child", "status": "overrides", "enabled_by": "base"}
    ]
    assert result["primitives"] == []


def test_disable_and_enable_in_same_layer_is_an_error(cases: Path) -> None:
    (cases / "loadouts/self.yaml").write_text(
        "schema_version: 2\nname: self\nskills: [helper]\ndisable: [skill:helper]\n"
    )
    with pytest.raises(ModelError) as error:
        resolve(cases, "self", "claude")
    assert codes(error) == {"disable-conflict"}


def test_stale_disable_warns_only_with_observed_state(cases: Path) -> None:
    unverified = resolve(cases, "stale-disable", "claude")
    assert unverified["disables"][0]["status"] == "external-unverified"
    assert unverified["warnings"] == []
    stale = resolve(cases, "stale-disable", "claude", observed=set())
    assert stale["disables"][0]["status"] == "stale"
    assert [w["code"] for w in stale["warnings"]] == ["stale-disable"]
    assert not any(e["source"] == "skill:old-helper" for e in stale["effects"])
    live = resolve(cases, "stale-disable", "claude", observed={"skill:old-helper"})
    assert live["disables"][0]["status"] == "external"


def test_cycle_is_an_error(cases: Path) -> None:
    with pytest.raises(ModelError) as error:
        resolve(cases, "cycle-a", "claude")
    assert codes(error) == {"cycle"}
    assert "cycle-a -> cycle-b -> cycle-a" in error.value.problems[0].message


def test_missing_parent_and_primitive_are_errors(cases: Path) -> None:
    (cases / "loadouts/orphan.yaml").write_text(
        "schema_version: 2\nname: orphan\nextends: [nowhere]\n"
    )
    with pytest.raises(ModelError) as error:
        resolve(cases, "orphan", "claude")
    assert "missing loadout 'nowhere'" in error.value.problems[0].message
    (cases / "loadouts/ghost.yaml").write_text(
        "schema_version: 2\nname: ghost\nskills: [skill:ghost]\n"
    )
    with pytest.raises(ModelError) as error:
        resolve(cases, "ghost", "claude")
    assert codes(error) == {"missing"}


def test_path_traversal_is_rejected(cases: Path) -> None:
    with pytest.raises(ModelError) as error:
        parse_loadout(cases, "traversal")
    assert codes(error) == {"path-traversal"}
    with pytest.raises(ModelError) as error:
        resolve(cases, "../traversal", "claude")
    assert codes(error) == {"path-traversal"}


def test_symlinked_primitive_outside_warehouse_is_rejected(cases: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.yaml"
    outside.write_text("schema_version: 2\nkind: skill\nname: linked\nexternal: true\n")
    link = cases / "primitives/skills/linked.yaml"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(ModelError) as error:
        parse_primitive(cases, "skill:linked")
    assert codes(error) == {"path-traversal"}


def test_credential_literal_is_rejected(cases: Path) -> None:
    with pytest.raises(ModelError) as error:
        resolve(cases, "credential", "claude")
    assert codes(error) == {"credential-literal"}
    assert "not-a-reference" not in str(error.value.problems)


def test_url_with_embedded_password_is_rejected(cases: Path) -> None:
    (cases / "primitives/mcp/remote.yaml").write_text(
        "schema_version: 2\nkind: mcp\nname: remote\ntransport: http\n"
        "url: https://user:pw-literal@example.invalid/mcp\n"
    )
    with pytest.raises(ModelError) as error:
        parse_primitive(cases, "mcp:remote")
    assert codes(error) == {"credential-literal"}


def test_env_references_compile_per_harness(cases: Path) -> None:
    (cases / "primitives/mcp/ref.yaml").write_text(
        "schema_version: 2\nkind: mcp\nname: ref\ncommand: example-server\n"
        "env: {API_KEY: {env: API_KEY}, OTHER: {env: RENAMED}}\n"
    )
    (cases / "loadouts/ref.yaml").write_text(
        "schema_version: 2\nname: ref\nmcp: {ref: {primitive: mcp:ref, enabled_tools: [a]}}\n"
    )
    claude = resolve(cases, "ref", "claude")
    assert claude["effects"][0]["value"]["env"] == {"API_KEY": "${API_KEY}", "OTHER": "${RENAMED}"}
    assert {g["capability"] for g in claude["gaps"]} == {"mcp_tool_filter"}
    codex = resolve(cases, "ref", "codex")
    assert codex["effects"][0]["value"]["env_vars"] == ["API_KEY"]
    assert codex["effects"][0]["value"]["enabled_tools"] == ["a"]
    assert "mcp_env_rename" in {g["capability"] for g in codex["gaps"]}


def test_unknown_fields_and_v1_shape(cases: Path) -> None:
    with pytest.raises(ModelError) as error:
        parse_loadout(cases, "unknown-field")
    assert codes(error) == {"unknown-field"}
    base = parse_loadout(cases, "base")
    assert base.schema_version == 1 and base.skills == ["skill:helper"]
    (cases / "primitives/skills/v3.yaml").write_text("schema_version: 3\nkind: skill\nname: v3\n")
    with pytest.raises(ModelError) as error:
        parse_primitive(cases, "skill:v3")
    assert "schema-version" in codes(error)


def test_duplicate_keys_are_rejected(cases: Path) -> None:
    (cases / "loadouts/dupe.yaml").write_text("schema_version: 2\nname: dupe\nname: dupe\n")
    with pytest.raises(ModelError) as error:
        parse_loadout(cases, "dupe")
    assert codes(error) == {"yaml"}


def test_package_servers_must_be_pinned(cases: Path) -> None:
    (cases / "primitives/mcp/drift.yaml").write_text(
        "schema_version: 2\nkind: mcp\nname: drift\ncommand: npx\nargs: [-y, some-mcp@latest]\n"
    )
    with pytest.raises(ModelError) as error:
        parse_primitive(cases, "mcp:drift")
    assert codes(error) == {"pin"}


def test_hash_covers_assets_and_ignores_line_endings(cases: Path) -> None:
    skill = parse_primitive(cases, "skill:helper")
    asset = cases / "primitives/skills/helper/SKILL.md"
    asset.write_bytes(asset.read_bytes().replace(b"\n", b"\r\n"))
    assert parse_primitive(cases, "skill:helper").content_hash == skill.content_hash
    asset.write_bytes(asset.read_bytes() + b"More help.\n")
    assert parse_primitive(cases, "skill:helper").content_hash != skill.content_hash
    assert skill.content_hash.startswith("kit-hash-v1:sha256:")


def test_recorded_hash_must_match(cases: Path) -> None:
    path = cases / "primitives/plugins/gen-a.yaml"
    primitive = parse_primitive(cases, "plugin:gen-a")
    path.write_text(path.read_text() + f"hash: {primitive.content_hash}\n")
    assert parse_primitive(cases, "plugin:gen-a").content_hash == primitive.content_hash
    path.write_text(path.read_text().replace("gen-a@market }", "gen-a@other }", 1))
    with pytest.raises(ModelError) as error:
        parse_primitive(cases, "plugin:gen-a")
    assert codes(error) == {"hash"}
    assert content_hash({"a": 1}, None) == content_hash({"a": 1, "hash": "x"}, None)


def test_validate_reports_every_broken_case(cases: Path) -> None:
    report = validate(cases)
    found = {p["code"] for p in report["problems"] if p["severity"] == "error"}
    assert {
        "slot-conflict",
        "cycle",
        "path-traversal",
        "credential-literal",
        "unknown-field",
    } <= found


def test_cli_resolve_reads_only(capsys: pytest.CaptureFixture[str]) -> None:
    before = snapshot(WAREHOUSE)
    code = main(
        ["resolve", "brand-studio", "--harness", "codex", "--warehouse", str(WAREHOUSE), "--json"]
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out)["harness"]["name"] == "codex"
    assert snapshot(WAREHOUSE) == before
    assert (
        main(["resolve", "brand-studio", "--harness", "claude", "--warehouse", str(WAREHOUSE)]) == 0
    )
    assert "gap [unsupported] marketplace:brand-builder" in capsys.readouterr().out


def test_cli_exit_codes(cases: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["resolve", "cycle-a", "--harness", "claude", "--warehouse", str(cases)]) == 1
    assert main(["validate", str(cases)]) == 1
    assert main(["validate", str(WAREHOUSE)]) == 0
    assert main(["validate", str(cases / "nope")]) == 2
    observed = cases / "observed-empty.json"
    assert (
        main(
            [
                "resolve",
                "stale-disable",
                "--harness",
                "claude",
                "--warehouse",
                str(cases),
                "--observed",
                str(observed),
            ]
        )
        == 0
    )
    assert "nothing enables it" in capsys.readouterr().err


def test_default_warehouse_is_kits_under_home(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("KIT_WAREHOUSE", raising=False)
    assert main(["validate"]) == 2
    monkeypatch.setenv("KIT_WAREHOUSE", str(WAREHOUSE))
    assert main(["validate"]) == 0


def test_external_mcp_without_launch_spec_is_a_gap_not_a_crash(tmp_path: Path) -> None:
    # An external MCP primitive skips kind checks; resolve must report it, not raise KeyError.
    (tmp_path / "primitives" / "mcp").mkdir(parents=True)
    (tmp_path / "loadouts").mkdir()
    (tmp_path / "primitives" / "mcp" / "linear.yaml").write_text(
        "schema_version: 2\nkind: mcp\nname: linear\ndescription: managed elsewhere\n"
        "external: true\nharnesses: { claude: {}, codex: {} }\n"
    )
    (tmp_path / "loadouts" / "flow.yaml").write_text(
        "schema_version: 2\nname: flow\ndescription: d\nextends: []\n"
        "mcp:\n  linear: { primitive: mcp:linear }\n"
    )
    report = validate(tmp_path)
    assert [p for p in report["problems"] if p["severity"] == "error"] == []
    for harness in ("claude", "codex"):
        result = resolve(tmp_path, "flow", harness)
        assert any(g["capability"] == "mcp_external" for g in result["gaps"])
