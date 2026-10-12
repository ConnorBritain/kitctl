import tomllib

from kitctl import tomlw


def test_round_trip_tables_and_arrays() -> None:
    document = {
        "schema_version": 1,
        "name": 'quote " and \\ backslash',
        "tags": ["a", "b"],
        "source": {"origin": "C:\\Users\\me\\x", "adopted_at": "2026-10-12T00:00:00Z"},
        "decision": [
            {"kind": "skill", "name": "writer", "locations": ["/p#abc"], "keep": True},
            {"kind": "mcp_server", "name": "my server", "locations": []},
        ],
    }
    assert tomllib.loads(tomlw.dumps(document, header="kitctl triage")) == document
