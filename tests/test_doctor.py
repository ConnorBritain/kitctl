import os
from pathlib import Path

import pytest

from kitctl.cli import main
from kitctl.doctor import installed_version, probe_version, run


def shim(directory: Path, name: str, output: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        (directory / f"{name}.cmd").write_text(f"@echo {output}\r\n")
    else:
        path = directory / name
        path.write_text(f"#!/bin/sh\necho '{output}'\n")
        path.chmod(0o755)


def test_versions_and_checks(
    harnesses: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    shim(bin_dir, "claude", "2.1.296 (Claude Code)")
    shim(bin_dir, "codex", "codex-cli 0.200.0")
    monkeypatch.setenv("PATH", str(bin_dir))
    assert probe_version("claude") == "2.1.296"
    result = run(probe=True)
    checks = {c["check"]: c for c in result["checks"]}
    assert checks["Claude Code"]["status"] == "ok"
    assert checks["Codex"]["status"] == "info" and "0.200.0" in checks["Codex"]["detail"]
    assert checks["hook latency"]["status"] == "warn"
    assert checks["kitctl on PATH"]["status"] == "warn"


def test_missing_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert installed_version("claude") == (None, "missing")
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "`claude` not found" in out and "`codex` not found" in out


def test_versions_read_from_install_layout_without_executing(tmp_path, monkeypatch) -> None:
    import json as _json

    native = tmp_path / "share/claude/versions/2.1.86"
    native.parent.mkdir(parents=True)
    native.write_text("binary")
    npm = tmp_path / "lib/node_modules/@openai/codex"
    (npm / "bin").mkdir(parents=True)
    (npm / "bin/codex.js").write_text("")
    (npm / "package.json").write_text(_json.dumps({"name": "@openai/codex", "version": "0.153.4"}))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    suffix = ".exe" if os.name == "nt" else ""
    try:
        (bin_dir / f"claude{suffix}").symlink_to(native)
        (bin_dir / f"codex{suffix}").symlink_to(npm / "bin/codex.js")
    except OSError:
        pytest.skip("symlinks unavailable")
    for target in (native, npm / "bin/codex.js"):
        target.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    if os.name == "nt":
        monkeypatch.setenv("PATHEXT", ".EXE")
    assert installed_version("claude") == ("2.1.86", "install path")
    assert installed_version("codex") == ("0.153.4", "package.json")
    (bin_dir / f"claude{suffix}").unlink()
    plain = bin_dir / f"claude{suffix}"
    plain.write_text("")
    plain.chmod(0o755)
    assert installed_version("claude") == ("", "unknown")
