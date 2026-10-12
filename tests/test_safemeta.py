from kitctl.safemeta import command_basename, frontmatter, iter_hooks, mcp_server, settings


def test_frontmatter_variants() -> None:
    text = '---\nname: "quoted"\ndescription: >\n  folded\n  text\nother: x\n---\nbody'
    assert frontmatter(text) == {"name": "quoted", "description": "folded text"}
    assert frontmatter("---\r\nname: crlf\r\n---\r\n") == {"name": "crlf"}
    assert frontmatter("no frontmatter") == {}


def test_command_basename_skips_assignments() -> None:
    assert command_basename("TOKEN=abc FOO=1 /usr/bin/tool --x") == "tool"
    assert command_basename('"C:\\Program Files\\x\\run.exe" arg') == "run.exe"
    assert command_basename("env A=b node script.js") == "node"
    assert command_basename("unbalanced 'quote") == "unbalanced"


def test_mcp_server_never_returns_values() -> None:
    meta = mcp_server(
        {
            "url": "https://user:pw@host.example:8443/p?k=v",
            "headers": {"X-Key": "v"},
            "env": {"A": "v"},
            "args": ["v"],
        }
    )
    assert meta == {
        "transport": "http",
        "url_host": "host.example",
        "header_names": ["X-Key"],
        "env_names": ["A"],
        "arg_count": 1,
    }
    assert mcp_server("nonsense") == {"invalid": True}


def test_settings_keys_only() -> None:
    meta = settings({"env": {"A": "secret"}, "enabledPlugins": {"p@m": True}})
    assert meta == {"keys": ["enabledPlugins", "env"], "enabled_plugins": {"p@m": True}}


def test_iter_hooks_tolerates_bad_shapes() -> None:
    assert list(iter_hooks({"PreToolUse": "nope"})) == []
    assert list(iter_hooks([1, 2])) == []
    hooks = list(
        iter_hooks({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "x"}]}]}})
    )
    assert hooks[0]["event"] == "Stop" and hooks[0]["matcher"] == "*"


def test_command_basename_keeps_spaces_in_program_path() -> None:
    meta = mcp_server({"command": "/Applications/Codex Computer Use.app/Contents/MacOS/cua"})
    assert meta["command"] == "cua"
    assert mcp_server({"command": "my server.exe"})["command"] == "my server.exe"


def test_disabled_codex_server_flag() -> None:
    assert mcp_server({"command": "x", "enabled": False})["disabled"] is True
