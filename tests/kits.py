"""A sandbox warehouse for write-engine tests."""

from __future__ import annotations

import json
from pathlib import Path


def write(path: Path, text: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")
    if mode is not None:
        path.chmod(mode)


def primitive(
    root: Path,
    folder: str,
    kind: str,
    name: str,
    extra: str = "",
    harnesses: str = '["claude", "codex"]',
) -> Path:
    directory = root / "primitives" / folder / name
    write(
        directory / "primitive.toml",
        f'schema_version = 1\nkind = "{kind}"\nname = "{name}"\nharnesses = {harnesses}\n{extra}',
    )
    return directory


def make_warehouse(root: Path) -> Path:
    skill = primitive(root, "skills", "skill", "writer")
    write(skill / "files/SKILL.md", "---\nname: writer\ndescription: Writes.\n---\nWrite well.\n")
    write(skill / "files/templates/note.md", "A template.\n")
    github = primitive(root, "mcp", "mcp", "github")
    write(
        github / "server.json",
        json.dumps(
            {
                "github": {
                    "command": "gh-mcp",
                    "args": ["--stdio"],
                    "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"},
                }
            }
        ),
    )
    docs = primitive(root, "mcp", "mcp", "docs")
    write(
        docs / "server.json",
        json.dumps(
            {
                "docs": {
                    "type": "http",
                    "url": "https://docs.example.com/mcp",
                    "headers": {"Authorization": "Bearer ${DOCS_TOKEN}"},
                }
            }
        ),
    )
    rules = primitive(root, "rules", "rules", "style")
    write(rules / "files/style.md", "# Style\nPrefer short sentences.\n")
    agent = primitive(root, "agents", "agent", "reviewer", harnesses='["claude"]')
    write(agent / "files/reviewer.md", "---\nname: reviewer\n---\nReview code.\n")
    hook = primitive(
        root,
        "hooks",
        "hook",
        "lint",
        extra='[hook]\nevent = "PreToolUse"\nmatcher = "Bash"\nscript = "lint.sh"\n',
    )
    write(hook / "files/lint.sh", "#!/bin/sh\nexit 0\n", mode=0o755)
    write(
        root / "loadouts/core.toml",
        'schema_version = 1\nname = "core"\nskills = ["writer"]\nmcp = ["github"]\n'
        'hooks = ["lint"]\nplugins = ["formatter@team-tools"]\n',
    )
    write(
        root / "loadouts/web.toml",
        'schema_version = 1\nname = "web"\nmcp = ["docs"]\nrules = ["style"]\n'
        'agents = ["reviewer"]\n',
    )
    return root


def snapshot(*roots: Path) -> dict[str, bytes]:
    """Every file (and directory, as b'<dir>') under ``roots``, for byte-exact comparisons."""
    found: dict[str, bytes] = {}
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            key = path.as_posix()
            found[key] = b"<dir>" if path.is_dir() else path.read_bytes()
    return found
