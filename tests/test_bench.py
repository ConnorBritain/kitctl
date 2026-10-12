import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_bench_small_runs(tmp_path: Path) -> None:
    out = tmp_path / "bench.json"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/bench.py"), "--small", "--json", str(out)],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    assert "inventory_warm" in out.read_text()
