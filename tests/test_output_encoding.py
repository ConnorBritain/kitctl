import os
import subprocess
import sys
from pathlib import Path


# Windows sends piped output through the ANSI code page (cp1252), and kitctl's help, plans and
# instruction blocks contain "—" and "→"; without UTF-8 output, every scripted or agent-driven
# run (`kitctl plan | more`, CI, a coding agent) dies before it can show its plan.
def test_piped_output_survives_a_non_utf8_code_page(tmp_path: Path) -> None:
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    env.pop("PYTHONUTF8", None)
    done = subprocess.run(
        [sys.executable, "-m", "kitctl", "--help"], capture_output=True, env=env, check=False
    )
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    assert "Kit — inventory".encode() in done.stdout
    missing = tmp_path / "a→b"
    failed = subprocess.run(
        [sys.executable, "-m", "kitctl", "inventory", "--root", str(missing)],
        capture_output=True,
        env=env,
        check=False,
    )
    assert failed.returncode == 2, failed.stderr.decode("utf-8", "replace")
    assert "a→b".encode() in failed.stderr
