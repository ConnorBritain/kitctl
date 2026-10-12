"""`kitctl plan`: turn triage decisions into a manual cleanup checklist (Markdown).

Nothing is applied. Commands use each harness's own CLI where one exists (verified:
`claude mcp remove -s <scope>`, `claude plugin disable|uninstall` on Claude Code 2.1.296;
`codex mcp remove` on Codex 0.153.4).
"""

from __future__ import annotations

import shlex
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kitctl.triage import Key

KIND_LIST = {
    "skill": "skills",
    "mcp_server": "mcp",
    "hook": "hooks",
    "plugin": "plugins",
    "agent": "agents",
    "command": "commands",
    "rules": "rules",
}


def _quote(text: str, windows: bool) -> str:
    return "'" + text.replace("'", "''") + "'" if windows else shlex.quote(text)


def _remove(path: str, windows: bool) -> str:
    quoted = _quote(path, windows)
    if windows:
        return f"Remove-Item -Recurse -Force {quoted}"
    return f"rm -r {quoted}" if Path(path).is_dir() else f"rm {quoted}"


def _in_git(path: str) -> bool:
    current = Path(path)
    return any((parent / ".git").exists() for parent in [current, *current.parents][:12])


class Planner:
    def __init__(self, entries: list[dict[str, Any]], windows: bool) -> None:
        self.windows = windows
        self.by_key: dict[Key, list[dict[str, Any]]] = defaultdict(list)
        for entry in entries:
            if not (entry["provided_by"] and "@" in entry["provided_by"]):
                self.by_key[(entry["kind"], entry["harness"], entry["name"])].append(entry)
        self.provided: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for entry in entries:
            if entry["provided_by"]:
                self.provided[entry["provided_by"]].append(entry)

    def q(self, text: str) -> str:
        return _quote(text, self.windows)

    # -- per-location steps ------------------------------------------------------------

    def retire_steps(self, entry: dict[str, Any]) -> list[str]:
        kind, harness, name, scope = entry["kind"], entry["harness"], entry["name"], entry["scope"]
        repo = f" (run inside `{entry['repo']}`)" if entry["repo"] and scope == "local" else ""
        if scope == "managed":
            return [f"Managed by your organization (`{entry['path']}`); ask an administrator."]
        if kind == "mcp_server":
            if scope == "plugin":
                return [f"Provided by plugin `{entry['provided_by']}`; retire the plugin instead."]
            if harness == "claude":
                return [f"`claude mcp remove {self.q(name)} -s {scope}`{repo}"]
            if scope == "user":
                return [f"`codex mcp remove {self.q(name)}`"]
            return [f"Edit `{entry['path']}` and delete the `[mcp_servers.{name}]` table."]
        if kind == "plugin":
            if harness == "claude":
                return [
                    f"`claude plugin disable {self.q(name)}` (reversible), then "
                    f"`claude plugin uninstall {self.q(name)}`"
                ]
            return [
                f"Remove with the Codex CLI (check `codex plugin --help`); cached copy at "
                f"`{entry['path']}`."
            ]
        if entry["provided_by"] == "claude.ai:synced":
            return [
                "Synced from your claude.ai account; turn it off there (local files are re-synced)."
            ]
        if entry["provided_by"] == "codex:system":
            return ["Bundled with Codex; it can't be removed separately."]
        if kind == "hook" and entry["metadata"].get("source") == "config":
            meta = entry["metadata"]
            return [
                f"Edit `{entry['path']}` and delete the `{meta.get('event')}` hook with "
                f"matcher `{meta.get('matcher')}` running `{meta.get('command')}`."
            ]
        steps = [
            f"`kitctl adopt {self.q(entry['path'])} --to attic` (archive a copy first)",
            f"`{_remove(entry['path'], self.windows)}`",
        ]
        if _in_git(entry["path"]):
            steps.append("The path is inside a git repository: commit the removal there.")
        return steps

    def user_target(self, kind: str, harness: str, name: str) -> str:
        if kind == "skill":
            return (
                f"~/.claude/skills/{name}/" if harness == "claude" else f"~/.agents/skills/{name}/"
            )
        if kind in {"agent", "command", "rules"}:
            folder = {"agent": "agents", "command": "commands", "rules": "rules"}[kind]
            return f"~/.claude/{folder}/{name}.md" if harness == "claude" else f"~/.codex/{folder}/"
        if kind == "mcp_server":
            return (
                "user scope (`claude mcp add -s user`)"
                if harness == "claude"
                else "user scope (`~/.codex/config.toml`)"
            )
        if kind == "plugin":
            return "user scope (installed and enabled)"
        return "user scope"


def build(
    decisions: dict[Key, dict[str, Any]], entries: list[dict[str, Any]], windows: bool
) -> str:
    planner = Planner(entries, windows)
    sections: dict[str, list[str]] = defaultdict(list)
    loadouts: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    changed: list[str] = []
    for key, decision in sorted(decisions.items()):
        kind, harness, name = key
        current = planner.by_key.get(key, [])
        recorded = {
            loc.rsplit("#", 1)[0]: loc.rsplit("#", 1)[-1] for loc in decision.get("locations", [])
        }
        live = {e["path"]: (e["content_hash"] or "")[:12] for e in current}
        if any(live.get(path) != digest for path, digest in recorded.items()) or set(live) - set(
            recorded
        ):
            changed.append(f"{kind} `{name}` ({harness})")
        title = f"### {kind} `{name}` ({harness})"
        choice = decision["decision"]
        lines = [title, f"_Reason: {decision.get('reason', '')}_", ""]
        if not current:
            lines.append("- [ ] No longer in the inventory; nothing to do.")
        elif choice == "retire":
            for entry in current:
                where = Path(entry["repo"]).name if entry["repo"] else entry["scope"]
                for step in planner.retire_steps(entry):
                    lines.append(f"- [ ] {where}: {step}")
            for provided in planner.provided.get(name, []) if kind == "plugin" else []:
                lines.append(f"  - also removes {provided['kind']} `{provided['name']}`")
        elif choice == "core":
            canonical = next((e for e in current if e["scope"] == "user"), current[0])
            lines.append(
                f"- [ ] Keep one copy at {planner.user_target(kind, harness, name)} "
                f"(canonical: `{canonical['path']}`)."
            )
            for entry in current:
                if entry is canonical:
                    continue
                note = (
                    "identical"
                    if entry["content_hash"] == canonical["content_hash"]
                    else "DIFFERS from canonical; review before removing"
                )
                for step in planner.retire_steps(entry):
                    lines.append(
                        f"- [ ] Remove duplicate ({note}) in "
                        f"{Path(entry['repo']).name if entry['repo'] else entry['scope']}: {step}"
                    )
            if kind == "plugin":
                lines.append("- [ ] Keep it installed; it is listed in the core loadout below.")
            elif kind != "mcp_server":
                lines.append(f"- [ ] `kitctl adopt {planner.q(canonical['path'])} --to primitives`")
            else:
                lines.append(
                    f"- [ ] `kitctl adopt mcp:{name} --harness {harness} --scope "
                    f"{canonical['scope']} --to primitives`"
                )
            loadouts["core"][KIND_LIST.get(kind, kind)].append(name)
        elif choice == "loadout":
            target = decision.get("loadout") or "core"
            source = current[0]
            if kind == "mcp_server":
                lines.append(f"- [ ] `kitctl adopt mcp:{name} --harness {harness} --to primitives`")
            elif kind == "plugin":
                lines.append("- [ ] Keep it installed only where needed (listed in the loadout).")
            else:
                lines.append(f"- [ ] `kitctl adopt {planner.q(source['path'])} --to primitives`")
            lines.append(
                f"- [ ] Add to `loadouts/{target}.toml` (snippet below). Keep repo "
                "copies until kitctl 0.2.0 can apply loadouts."
            )
            loadouts[target][KIND_LIST.get(kind, kind)].append(name)
        elif choice == "merge":
            canonical_path = decision.get("canonical") or current[0]["path"]
            canonical_hash = next(
                (e["content_hash"] for e in current if e["path"] == canonical_path), None
            )
            # Identical copies of the canonical variant stay (e.g. one .mcp.json per repo).
            others = [e for e in current if e["content_hash"] != canonical_hash]
            lines.append(f"- [ ] Canonical copy: `{canonical_path}`")
            for entry in others:
                lines.append(
                    f"- [ ] Compare: `diff -ru {planner.q(canonical_path)} "
                    f"{planner.q(entry['path'])}` and fold anything worth keeping in."
                )
            for entry in others:
                for step in planner.retire_steps(entry):
                    lines.append(f"- [ ] Then retire `{entry['path']}`: {step}")
        else:
            sections["none"].append(f"- {kind} `{name}` ({harness}): {choice}")
            continue
        sections[choice].append("\n".join(lines))

    out = [
        "# kitctl cleanup plan",
        "",
        f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC from triage.toml. **Nothing has been "
        "applied.** Every step is manual: back up first (or commit), work top to bottom, and "
        "run `kitctl report` afterwards to confirm.",
        "",
    ]
    if changed:
        out += ["## Changed since triage (re-check before acting)", ""]
        out += [f"- {item}" for item in changed] + [""]
    titles = {
        "retire": "Retire",
        "merge": "Merge differing copies",
        "core": "Promote to core",
        "loadout": "Move into loadouts",
    }
    for choice, title in titles.items():
        if sections.get(choice):
            out += [f"## {title} ({len(sections[choice])})", "", *sections[choice], ""]
    if loadouts:
        out += [
            "## Loadout snippets",
            "",
            "Add these to the warehouse (merge with existing files), then run `kitctl validate`:",
            "",
        ]
        for loadout, kinds in sorted(loadouts.items()):
            out.append(f"`loadouts/{loadout}.toml`:")
            out.append("```toml")
            out.append(f'schema_version = 1\nname = "{loadout}"')
            for list_name, names in sorted(kinds.items()):
                out.append(
                    f"{list_name} = [" + ", ".join(f'"{n}"' for n in sorted(set(names))) + "]"
                )
            out += ["```", ""]
    if sections.get("none"):
        out += ["## No action", "", *sections["none"], ""]
    return "\n".join(out)
