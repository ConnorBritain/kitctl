"""`kitctl init`: a starter warehouse, so a new user can try Kit without someone else's."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

KINDS = ("skills", "agents", "mcp", "hooks", "plugins", "marketplaces", "tools")

FILES = {
    "README.md": """# My kit warehouse

Created by `kitctl init`. kitctl reads loadouts from here and writes each harness's own
config in the repos you point it at.

- `loadouts/`: named bundles. `starter` is a harmless first one: a short instruction block
  and a check that git is installed.
- `primitives/<kind>/`: the skills, agents, MCP servers, hooks, plugins, marketplaces and
  tools loadouts refer to (`kind:name`). The format is kitctl's docs/model.md.

Try it in a repo:

```sh
kitctl validate
kitctl use starter --dry-run   # the plan, nothing written
kitctl use starter             # the plan, then [y/N]
kitctl off starter             # the undo: restores the original bytes
```

Credentials are environment variable names only, never values. Commit this directory to a
private repository; it is your configuration.
""",
    "loadouts/starter.yaml": """schema_version: 2
name: starter
description: A first loadout to try kitctl with; it changes only the instruction files.
extends: []
requires: [tool:git]
instructions: |
  ## Kit   <!-- kitctl -->
  This repo's agent configuration is managed by kitctl. `kitctl status` shows what is on;
  `kitctl off <loadout>` removes a loadout and restores what was there before.
""",
    "primitives/tools/git.yaml": """schema_version: 2
kind: tool
name: git
description: Git, so each change Kit makes can be reviewed and committed.
check: { command: [git, --version], expect: "^git version " }
install_hint: "https://git-scm.com/downloads"
""",
    ".gitignore": "reports/\n",
}


class InitError(Exception):
    pass


def init_warehouse(root: Path, git: bool = True) -> list[str]:
    """Create a starter warehouse at root. Refuses a non-empty directory. Returns what it made."""
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise InitError(f"{root} already exists and is not empty; choose another path")
    made = []
    for kind in KINDS:
        (root / "primitives" / kind).mkdir(parents=True, exist_ok=True)
    (root / "loadouts").mkdir(parents=True, exist_ok=True)
    for rel, text in FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        made.append(rel)
    for kind in KINDS:
        if not any((root / "primitives" / kind).iterdir()):
            (root / "primitives" / kind / ".gitkeep").write_text("", encoding="utf-8")
    if git and shutil.which("git"):
        done = subprocess.run(
            ["git", "init", "-q", str(root)], capture_output=True, text=True, check=False
        )
        if done.returncode == 0:
            made.append(".git (git init)")
    return made
