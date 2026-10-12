import subprocess
import sys
from pathlib import Path


def test_staged_secret_check_uses_index_and_withholds_values(sandbox: Path) -> None:
    checker = Path(__file__).resolve().parents[1] / "scripts/check_secrets.py"
    subprocess.run(["git", "init", "-q"], cwd=sandbox, check=True)
    token = "ghp_" + "A" * 36
    candidate = sandbox / "config.txt"
    candidate.write_text(token)
    subprocess.run(["git", "add", "config.txt"], cwd=sandbox, check=True)
    # Cleaning the working tree must not hide a secret still in the staging area.
    candidate.write_text("clean")
    result = subprocess.run(
        [sys.executable, str(checker), "--staged"],
        cwd=sandbox,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "config.txt" in result.stderr
    assert token not in result.stderr + result.stdout
    subprocess.run(["git", "add", "config.txt"], cwd=sandbox, check=True)
    result = subprocess.run(
        [sys.executable, str(checker), "--staged"],
        cwd=sandbox,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0


# Issue 6: 0.0.1 missed these credential shapes.
SAMPLES = {
    "anthropic-key": "sk-ant-" + "api03-" + "x" * 40,
    "openai-key": "sk-proj-" + "Y" * 40,
    "slack-token": "xoxb-" + "1234567890-abcdefghij",
    "azure-connection": "DefaultEndpointsProtocol=https;AccountKey=" + "Q" * 40 + "==",
    "jwt": "eyJ" + "hbGciOiJIUzI1NiJ9" + ".eyJ" + "zdWIiOiJzZXJ2aWNlIn0" + "." + "s" * 30,
    "bearer-token": "Authorization: Bearer " + "t" * 32,
}


def test_detector_covers_new_kinds() -> None:
    from kitctl.secretscan import find, find_bytes

    for kind, sample in SAMPLES.items():
        assert kind in find(sample), kind
        assert kind in find_bytes(sample.encode()), kind


def test_bearer_only_in_config_context() -> None:
    from kitctl.secretscan import find, is_config_name

    assert "bearer-token" not in find(SAMPLES["bearer-token"], config_context=False)
    assert is_config_name(".mcp.json") and is_config_name(".env.local")
    assert not is_config_name("README.md")


def test_redact_obj_withholds_values() -> None:
    from kitctl.secretscan import redact_obj

    cleaned, kinds = redact_obj({"a": [SAMPLES["slack-token"]], "b": 3})
    assert SAMPLES["slack-token"] not in str(cleaned)
    assert cleaned["b"] == 3 and kinds == ["slack-token"]


def test_repo_check_flags_new_kinds_without_values(sandbox: Path) -> None:
    checker = Path(__file__).resolve().parents[1] / "scripts/check_secrets.py"
    subprocess.run(["git", "init", "-q"], cwd=sandbox, check=True)
    (sandbox / "settings.json").write_text(SAMPLES["anthropic-key"])
    subprocess.run(["git", "add", "settings.json"], cwd=sandbox, check=True)
    result = subprocess.run(
        [sys.executable, str(checker), "--staged"],
        cwd=sandbox,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "anthropic-key" in result.stderr
    assert SAMPLES["anthropic-key"] not in result.stderr + result.stdout


def test_redact_fast_path_matches_full_scan() -> None:
    from kitctl.secretscan import redact

    for sample in [*SAMPLES.values(), "short", "a" * 64, "/Users/me/Code/app/.mcp.json"]:
        text, kinds = redact(sample)
        assert (text != sample) == bool(kinds)
    assert redact(SAMPLES["jwt"])[1] == ["jwt"]
