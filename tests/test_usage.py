import json
from pathlib import Path

import pytest

from kitctl.cli import main
from kitctl.paths import resolve
from kitctl.usage import Event, attribute
from kitctl.usage import claude as claude_usage
from kitctl.usage.cache import MAX_LINE, UsageCache
from kitctl.usage.run import mine
from tests.conftest import CANARY


def records(result: dict, **criteria: object) -> list[dict]:
    return [r for r in result["records"] if all(r.get(k) == v for k, v in criteria.items())]


def test_claude_transcript_events_and_privacy(harnesses: Path) -> None:
    result = mine(resolve(), [harnesses])
    assert CANARY not in json.dumps(result)
    repo = str(harnesses)
    assert records(result, kind="skill", name="writer", repo=repo)[0]["calls"] == 2
    assert records(result, kind="mcp_server", name="github")[0]["calls"] == 2
    assert records(result, kind="agent", name="reviewer")[0]["calls"] == 1
    assert records(result, kind="command", name="ship")[0]["calls"] == 1
    assert records(result, kind="skill", name="deploy-tools:deploy")[0]["calls"] == 1
    assert not records(result, name="Bash")
    sentry = records(result, kind="mcp_server", name="sentry")[0]
    assert sentry["repo"] is None and sentry["last_used"].startswith("2026-09-01")
    stats = result["adapters"]["claude"]
    assert stats["malformed_total"] == 1 and stats["unknown_shape_total"] == 1


def test_codex_session_events_fail_soft(harnesses: Path) -> None:
    result = mine(resolve(), [harnesses])
    codex = [r for r in result["records"] if r["harness"] == "codex"]
    assert {(r["name"], r["repo"]) for r in codex} == {
        ("docs", str(harnesses)),
        ("remote", str(harnesses)),
    }
    assert result["adapters"]["codex"]["unknown_shape_total"] == 1
    assert CANARY not in json.dumps(codex)


def test_since_filter(harnesses: Path) -> None:
    result = mine(resolve(), [harnesses], since="2026-09-15")
    assert not records(result, name="sentry")
    assert records(result, name="github")


def write_lines(path: Path, lines: list[dict], mode: str = "a") -> None:
    with path.open(mode, encoding="utf-8") as handle:
        for line in lines:
            handle.write(json.dumps(line) + "\n")


def skill_line(name: str, ts: str = "2026-10-01T00:00:00Z") -> dict:
    return {
        "type": "assistant",
        "timestamp": ts,
        "cwd": "/w",
        "message": {"content": [{"type": "tool_use", "name": "Skill", "input": {"skill": name}}]},
    }


def test_incremental_cache_reads_only_new_bytes(tmp_path: Path) -> None:
    log = tmp_path / "s.jsonl"
    write_lines(log, [skill_line("a"), skill_line("b")], "w")
    store = tmp_path / "cache.json"
    cache = UsageCache(store, "claude", 1)
    first = cache.scan([log], claude_usage.parse)
    cache.save()
    assert first.lines == 2
    warm = UsageCache(store, "claude", 1)
    stats = warm.scan([log], claude_usage.parse)
    assert stats.bytes_read == 0 and stats.files_read == 0
    write_lines(log, [skill_line("a")])
    appended = warm.scan([log], claude_usage.parse)
    assert appended.lines == 1
    counts = {k.split("\x1f")[1]: v[0] for k, v in warm.files[str(log)].agg.items()}
    assert counts == {"a": 2, "b": 1}


def test_truncation_triggers_full_reread(tmp_path: Path) -> None:
    log = tmp_path / "s.jsonl"
    write_lines(log, [skill_line("a")] * 3, "w")
    cache = UsageCache(None, "claude", 1)
    cache.scan([log], claude_usage.parse)
    write_lines(log, [skill_line("z")], "w")
    cache.scan([log], claude_usage.parse)
    counts = {k.split("\x1f")[1]: v[0] for k, v in cache.files[str(log)].agg.items()}
    assert counts == {"z": 1}


def test_partial_trailing_line_waits_for_completion(tmp_path: Path) -> None:
    log = tmp_path / "s.jsonl"
    text = json.dumps(skill_line("late"))
    log.write_text(json.dumps(skill_line("a")) + "\n" + text[:20], encoding="utf-8")
    cache = UsageCache(None, "claude", 1)
    stats = cache.scan([log], claude_usage.parse)
    assert stats.malformed == 0 and stats.lines == 1
    with log.open("a", encoding="utf-8") as handle:
        handle.write(text[20:] + "\n")
    cache.scan([log], claude_usage.parse)
    names = {k.split("\x1f")[1] for k in cache.files[str(log)].agg}
    assert names == {"a", "late"}


def test_oversized_line_is_skipped_not_buffered(tmp_path: Path) -> None:
    log = tmp_path / "s.jsonl"
    huge = {"type": "assistant", "pad": "x" * (MAX_LINE * 2), "tool_use": 1}
    write_lines(log, [huge, skill_line("ok")], "w")
    cache = UsageCache(None, "claude", 1)
    stats = cache.scan([log], claude_usage.parse)
    assert stats.oversized == 1
    assert {k.split("\x1f")[1] for k in cache.files[str(log)].agg} == {"ok"}


def test_adapter_version_change_invalidates_cache(tmp_path: Path) -> None:
    log = tmp_path / "s.jsonl"
    write_lines(log, [skill_line("a")], "w")
    store = tmp_path / "cache.json"
    cache = UsageCache(store, "claude", 1)
    cache.scan([log], claude_usage.parse)
    cache.save()
    assert UsageCache(store, "claude", 2).files == {}


@pytest.mark.parametrize(
    ("cwd", "expected"),
    [
        ("/code/app", "/code/app"),
        ("/code/app/packages/api", "/code/app"),
        ("/code/app/nested", "/code/app/nested"),
        ("/code/application", None),
        ("/CODE/APP/src", "/code/app"),
        ("", None),
    ],
)
def test_attribution(cwd: str, expected: str | None) -> None:
    repos = [Path("/code/app"), Path("/code/app/nested")]
    result = attribute(cwd, repos)
    assert (result and Path(result).as_posix()) == (expected and Path(expected).as_posix())


def test_attribution_nfc() -> None:
    import unicodedata

    nfd = unicodedata.normalize("NFD", "/code/café")
    assert attribute(nfd + "/x", [Path("/code/café")]) is not None


def test_event_identity_only() -> None:
    assert set(Event.__dataclass_fields__) == {"kind", "name", "timestamp", "cwd", "detail"}


def test_usage_cli(harnesses: Path, capsys: pytest.CaptureFixture) -> None:
    assert main(["usage", "--root", str(harnesses)]) == 0
    out = capsys.readouterr().out
    assert "writer" in out and CANARY not in out
    assert main(["usage", "--root", str(harnesses), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["records"]
