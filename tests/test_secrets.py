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
