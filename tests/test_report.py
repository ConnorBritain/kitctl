import re
from pathlib import Path

import pytest

from kitctl import report as kit_report
from kitctl.cli import main
from kitctl.paths import resolve
from tests.conftest import CANARY, validate


def test_report_json_validates_and_has_sections(harnesses: Path) -> None:
    built = kit_report.build([harnesses])
    validate(built)
    assert {"usage", "analysis", "matrix"} <= set(built)
    writer = [r for r in built["matrix"] if r["kind"] == "skill" and r["name"] == "writer"]
    # user and project copies both match the same usage; calls are not double counted
    assert writer[0]["calls"] == 2 and len(writer[0]["locations"]) >= 2
    github = [r for r in built["matrix"] if r["kind"] == "mcp_server" and r["name"] == "github"]
    assert github[0]["calls"] == 2
    assert any(d["kind"] == "skill" for d in built["analysis"]["duplicates"])
    codes = {f["code"] for f in built["findings"]}
    assert {"plugin-enabled-not-installed", "usage-unknown-shape"} <= codes


@pytest.mark.parametrize("fmt", ["md", "json", "html"])
def test_report_formats_written_to_cache(
    harnesses: Path, fmt: str, capsys: pytest.CaptureFixture
) -> None:
    assert main(["report", "--root", str(harnesses), "--format", fmt]) == 0
    target = Path(capsys.readouterr().out.strip())
    assert target.parent == resolve().report_dir and target.suffix == f".{fmt}"
    text = target.read_text("utf-8")
    assert CANARY not in text
    assert "writer" in text
    if fmt == "html":
        assert not re.search(r"""(?:src|href)\s*=\s*["']?https?:""", text)
        assert "<table id=matrix" in text
    if fmt == "md":
        assert "## Inventory matrix" in text


def test_report_refuses_repo_output(harnesses: Path, tmp_path: Path) -> None:
    assert main(["report", "--root", str(harnesses), "--out", str(harnesses / "out")]) == 2
    assert not (harnesses / "out").exists()
    outside = tmp_path / "reports"
    assert main(["report", "--root", str(harnesses), "--out", str(outside)]) == 0
    assert list(outside.glob("report-*.md"))
