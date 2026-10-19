"""Secrets readiness: referenced ${NAME}s are reported by name, never by value."""

from __future__ import annotations

import json
from pathlib import Path

from kitctl.cli import main
from tests.conftest import CANARY
from tests.kits import make_warehouse


def test_apply_and_doctor_report_unset_references(sandbox: Path, tmp_path: Path,
                                                  monkeypatch, capsys) -> None:  # fmt: skip
    warehouse = make_warehouse(tmp_path / "home/kits")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert main(["init", "--non-interactive", "--warehouse", str(warehouse),
                 "--repo-parent", str(tmp_path)]) == 0  # fmt: skip
    capsys.readouterr()
    assert main(["apply", "--global", "--dry-run"]) == 0
    assert "GITHUB_TOKEN is not set" in capsys.readouterr().out
    assert main(["doctor", "--json"]) in (0, 1)
    checks = {c["check"]: c for c in json.loads(capsys.readouterr().out)["checks"]}
    assert checks["MCP environment"]["status"] == "warn"
    assert "GITHUB_TOKEN" in checks["MCP environment"]["detail"]

    monkeypatch.setenv("GITHUB_TOKEN", CANARY)
    assert main(["apply", "--global", "--dry-run"]) == 0
    applied = capsys.readouterr().out
    assert CANARY not in applied and "GITHUB_TOKEN is not set" not in applied
    assert main(["doctor", "--json"]) in (0, 1)
    out = capsys.readouterr().out
    assert CANARY not in out
    checks = {c["check"]: c for c in json.loads(out)["checks"]}
    assert checks["MCP environment"]["status"] == "ok"
