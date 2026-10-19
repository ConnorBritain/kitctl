"""Forge: brief -> (agent research) -> candidates -> proposed loadout -> verified.

`forge new` writes a brief. The research itself is agent work guided by the
kit-forge skill; its output, candidates.yaml, is validated here: facts must
come from `kitctl scout`, and every judgment must cite evidence that exists.
`forge propose` deterministically assembles a draft loadout in inbox/ from
adopt/vendor verdicts that pass the brief's constraints, and writes gaps.md.
`forge verify` runs the evals the brief declares, without a shell.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import subprocess
from io import StringIO
from pathlib import Path, PurePosixPath
from typing import Any

from ruamel.yaml import YAML

from kitctl.model import (
    HARNESSES,
    KIND_DIRS,
    NAME_RE,
    SLOT_RE,
    content_hash,
    is_traversal,
    load_yaml,
)
from kitctl.scout import DEFAULT_ALLOW, ScoutError, find_scouted

BRIEF_KEYS = {
    "schema_version",
    "slug",
    "purpose",
    "harnesses",
    "target_repos",
    "constraints",
    "required_slots",
    "exclusions",
    "evals",
}
CONSTRAINT_KEYS = {"licenses", "no_telemetry", "no_runtime_network", "no_install_scripts"}
CANDIDATE_KEYS = {
    "id",
    "source",
    "facts",
    "fills",
    "overlaps",
    "opinion_bias",
    "conflicts",
    "verdict",
    "config",
    "evidence",
    "notes",
}
JUDGMENTS = ("fills", "overlaps", "opinion_bias", "verdict", "config")
VERDICTS = {"adopt", "vendor", "borrow-ideas", "avoid"}
BIAS = {"none", "low", "high"}
FACT_KEYS = {
    "license",
    "last_commit",
    "manifests",
    "install_scripts",
    "telemetry",
    "outbound_network",
    "self_fetching",
    "aggressive_triggers",
}
CONFIG_KINDS = {"skill", "mcp", "hook", "plugin", "marketplace"}
EVIDENCE_RE = re.compile(r"^(?P<file>[^:\s]+):(?P<line>\d+)$")


class ForgeError(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def forge_dir(warehouse: Path, slug: str) -> Path:
    if not NAME_RE.match(slug) or "/" in slug:
        raise ForgeError([f"invalid forge slug {slug!r}"])
    return warehouse / "forge" / slug


def _dump(data: Any) -> str:
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.indent(mapping=2, sequence=4, offset=2)
    stream = StringIO()
    yaml.dump(data, stream)
    return stream.getvalue()


def new_brief(
    warehouse: Path,
    slug: str,
    purpose: str,
    *,
    harnesses: list[str] | None = None,
    target_repos: list[str] | None = None,
    required_slots: list[str] | None = None,
    exclusions: list[str] | None = None,
    allow: list[str] | None = None,
) -> Path:
    directory = forge_dir(warehouse, slug)
    path = directory / "brief.yaml"
    if path.exists():
        raise ForgeError([f"{path} already exists"])
    brief = {
        "schema_version": 1,
        "slug": slug,
        "purpose": purpose,
        "harnesses": harnesses or list(HARNESSES),
        "target_repos": target_repos or [],
        "constraints": {
            "licenses": {"allow": allow or list(DEFAULT_ALLOW), "deny": []},
            "no_telemetry": True,
            "no_runtime_network": True,
            "no_install_scripts": False,
        },
        "required_slots": required_slots or [],
        "exclusions": exclusions or [],
        "evals": [],
    }
    problems = check_brief(brief)
    if problems:
        raise ForgeError(problems)
    directory.mkdir(parents=True, exist_ok=True)
    path.write_text(_dump(brief), encoding="utf-8")
    return path


def check_brief(brief: Any) -> list[str]:
    problems: list[str] = []
    if not isinstance(brief, dict):
        return ["brief: expected a mapping"]
    problems += [f"brief.{k}: unknown field" for k in sorted(set(brief) - BRIEF_KEYS)]
    if brief.get("schema_version") != 1:
        problems.append("brief.schema_version: expected 1")
    if not isinstance(brief.get("purpose"), str) or not brief["purpose"].strip():
        problems.append("brief.purpose: required")
    if not set(brief.get("harnesses", [])) <= set(HARNESSES):
        problems.append("brief.harnesses: claude and/or codex")
    constraints = brief.get("constraints", {})
    if not isinstance(constraints, dict):
        problems.append("brief.constraints: expected a mapping")
    else:
        problems += [
            f"brief.constraints.{k}: unknown field"
            for k in sorted(set(constraints) - CONSTRAINT_KEYS)
        ]
    for slot in brief.get("required_slots", []):
        if not isinstance(slot, str) or not SLOT_RE.match(slot):
            problems.append(f"brief.required_slots: bad slot {slot!r}")
    for index, item in enumerate(brief.get("evals", [])):
        where = f"brief.evals[{index}]"
        if not isinstance(item, dict) or set(item) - {"name", "run", "cwd", "expect", "timeout"}:
            problems.append(f"{where}: expected name, run, cwd, expect, timeout")
            continue
        if not isinstance(item.get("name"), str):
            problems.append(f"{where}.name: required")
        run = item.get("run")
        if not isinstance(run, list) or not run or not all(isinstance(a, str) for a in run):
            problems.append(f"{where}.run: expected a command list (no shell)")
    return problems


def load_brief(warehouse: Path, slug: str) -> dict[str, Any]:
    path = forge_dir(warehouse, slug) / "brief.yaml"
    if not path.is_file():
        raise ForgeError([f"no brief at {path}; run `kitctl forge new {slug}` first"])
    brief = load_yaml(path)
    problems = check_brief(brief)
    if problems:
        raise ForgeError(problems)
    return dict(brief)


# Candidates --------------------------------------------------------------------------------------


def _check_evidence(
    items: Any, where: str, clone: Path | None, report: dict[str, Any] | None
) -> list[str]:
    """Each citation: file:line in the quarantine clone, fact:<key>, or an https URL."""
    if not isinstance(items, list) or not items:
        return [f"{where}: uncited claim (needs at least one evidence entry)"]
    problems: list[str] = []
    for item in items:
        if not isinstance(item, str):
            problems.append(f"{where}: evidence must be strings")
        elif item.startswith("https://"):
            continue
        elif item.startswith("fact:"):
            if report is None or item[5:] not in FACT_KEYS or item[5:] not in report:
                problems.append(f"{where}: {item} is not a scout fact")
        elif match := EVIDENCE_RE.match(item):
            file, line = match.group("file"), int(match.group("line"))
            if clone is None or is_traversal(file):
                problems.append(f"{where}: {item} cannot be checked")
                continue
            path = clone / PurePosixPath(file)
            try:
                count = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
            except OSError:
                problems.append(f"{where}: {item} does not exist in the quarantined clone")
                continue
            if not 1 <= line <= count:
                problems.append(f"{where}: {item} is past the end of the file ({count} lines)")
        else:
            problems.append(f"{where}: {item!r} is not file:line, fact:<key> or an https URL")
    return problems


def load_candidates(
    warehouse: Path, slug: str, cache: Path
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Validate candidates.yaml against scout facts. Returns (candidates, scout reports)."""
    path = forge_dir(warehouse, slug) / "candidates.yaml"
    if not path.is_file():
        raise ForgeError([f"no candidates at {path}; the research step writes it"])
    data = load_yaml(path)
    if (
        not isinstance(data, dict)
        or data.get("schema_version") != 1
        or set(data) - {"schema_version", "candidates"}
    ):
        raise ForgeError([f"{path}: expected schema_version: 1 and candidates"])
    problems: list[str] = []
    reports: dict[str, dict[str, Any]] = {}
    candidates = data.get("candidates") or []
    seen: set[str] = set()
    for index, candidate in enumerate(candidates):
        where = f"candidates[{index}]"
        if not isinstance(candidate, dict):
            problems.append(f"{where}: expected a mapping")
            continue
        problems += [f"{where}.{k}: unknown field" for k in sorted(set(candidate) - CANDIDATE_KEYS)]
        ident = candidate.get("id")
        if not isinstance(ident, str) or ident in seen:
            problems.append(f"{where}.id: missing or duplicate")
            continue
        seen.add(ident)
        where = f"candidates[{ident}]"
        try:
            clone, report = find_scouted(ident, cache)
        except ScoutError as error:
            problems.append(f"{where}: {error}")
            clone, report = None, None
        if report is not None:
            reports[ident] = report
            source = candidate.get("source", {})
            if source and (source.get("sha") != report["sha"]):
                problems.append(f"{where}.source.sha: does not match the scouted commit")
            for key, value in (candidate.get("facts") or {}).items():
                if key not in FACT_KEYS:
                    problems.append(f"{where}.facts.{key}: not a scout fact")
                elif value != report.get(key):
                    problems.append(
                        f"{where}.facts.{key}: facts come only from scout; "
                        "this value differs from the scout report"
                    )
        verdict = candidate.get("verdict")
        if verdict not in VERDICTS:
            problems.append(f"{where}.verdict: expected one of {sorted(VERDICTS)}")
        if candidate.get("opinion_bias", "none") not in BIAS:
            problems.append(f"{where}.opinion_bias: expected none, low or high")
        for slot in candidate.get("fills", []) or []:
            if not isinstance(slot, str) or not SLOT_RE.match(slot):
                problems.append(f"{where}.fills: bad slot {slot!r}")
        config = candidate.get("config")
        if verdict in {"adopt", "vendor"}:
            if (
                not isinstance(config, dict)
                or config.get("kind") not in CONFIG_KINDS
                or not (isinstance(config.get("name"), str) and NAME_RE.match(config["name"]))
            ):
                problems.append(f"{where}.config: adopt/vendor needs kind and name")
            elif verdict == "vendor" and not isinstance(config.get("path"), str):
                problems.append(f"{where}.config.path: vendor needs the path to copy")
        evidence = candidate.get("evidence") or {}
        if not isinstance(evidence, dict):
            problems.append(f"{where}.evidence: expected a mapping of field to citations")
            evidence = {}
        for field in JUDGMENTS:
            # Any judgment that is stated (including opinion_bias: none) must be cited.
            if candidate.get(field) in (None, [], {}):
                continue
            problems += _check_evidence(
                evidence.get(field), f"{where}.evidence.{field}", clone, report
            )
    if problems:
        raise ForgeError(problems)
    return list(candidates), reports


# Propose -----------------------------------------------------------------------------------------


def _excluded(
    candidate: dict[str, Any], report: dict[str, Any], brief: dict[str, Any]
) -> list[str]:
    reasons: list[str] = []
    constraints = brief.get("constraints", {})
    licenses = constraints.get("licenses", {})
    allow = licenses.get("allow", DEFAULT_ALLOW)
    if report["license"] in {"NOASSERTION", "CONFLICT"}:
        reasons.append(f"no clear license ({report['license']})")
    elif report["license"] in licenses.get("deny", []) or report["license"] not in allow:
        reasons.append(f"license {report['license']} not allowed")
    mitigations = " ".join(str(a) for a in (candidate.get("config") or {}).get("args", []))

    def cite(key: str) -> str:
        first = report[key][0]["evidence"]
        return f"{first['file']}:{first['line']}"

    if constraints.get("no_telemetry", True) and report["telemetry"]:
        unmitigated = [
            t
            for t in report["telemetry"]
            if not any(
                flag in t["evidence"]["text"]
                for flag in mitigations.split()
                if flag.startswith("--")
            )
        ]
        if unmitigated:
            first = unmitigated[0]["evidence"]
            reasons.append(f"telemetry ({first['file']}:{first['line']})")
    if constraints.get("no_runtime_network", True):
        for key in ("outbound_network", "self_fetching"):
            if report[key]:
                reasons.append(f"{key.replace('_', ' ')} ({cite(key)})")
    if constraints.get("no_install_scripts") and report["install_scripts"]:
        reasons.append(f"install scripts ({cite('install_scripts')})")
    for item in brief.get("exclusions", []):
        if item and (
            item in candidate["id"] or item == (candidate.get("config") or {}).get("name")
        ):
            reasons.append(f"excluded by the brief ({item})")
    return reasons


LIST_FOR_KIND = {
    "skill": "skills",
    "hook": "hooks",
    "plugin": "plugins",
    "marketplace": "marketplaces",
}
PASSTHROUGH = {
    "mcp": {"transport", "command", "url", "pin", "args", "env", "headers"},
    "hook": {"event", "matcher", "command", "timeout", "blocking", "unless_capability"},
    "plugin": {"marketplace", "plugin_name", "version", "skills", "hook_provides"},
    "skill": set(),
    "marketplace": set(),
}


def propose(warehouse: Path, slug: str, cache: Path) -> dict[str, Any]:
    brief = load_brief(warehouse, slug)
    candidates, reports = load_candidates(warehouse, slug, cache)
    accepted: list[dict[str, Any]] = []
    excluded: list[tuple[str, list[str]]] = []
    ideas: list[dict[str, Any]] = []
    pending: list[str] = []
    for candidate in candidates:
        verdict = candidate["verdict"]
        if verdict == "borrow-ideas":
            ideas.append(candidate)
            continue
        if verdict == "avoid":
            excluded.append((candidate["id"], ["verdict: avoid"]))
            continue
        reasons = _excluded(candidate, reports[candidate["id"]], brief)
        if reasons:
            excluded.append((candidate["id"], reasons))
        else:
            accepted.append(candidate)

    providers: dict[str, list[str]] = {}
    for candidate in accepted:
        for slot in candidate.get("fills", []) or []:
            providers.setdefault(slot, []).append(candidate["id"])
    problems = [
        f"slot {slot} has {len(ids)} providers: {' and '.join(ids)}; choose one"
        for slot, ids in sorted(providers.items())
        if len(ids) > 1 and slot in brief.get("required_slots", [])
    ]
    if problems:
        raise ForgeError(problems)

    loadout: dict[str, Any] = {
        "schema_version": 2,
        "name": slug,
        "description": brief["purpose"].strip(),
    }
    slots: dict[str, str] = {}
    inbox = warehouse / "inbox"
    written: list[str] = []
    for candidate in accepted:
        config = candidate["config"]
        kind, name = config["kind"], config["name"]
        ref = f"{kind}:{name}"
        report = reports[candidate["id"]]
        meta_path = inbox / KIND_DIRS[kind] / f"{name}.yaml"
        if candidate["verdict"] == "vendor":
            if not meta_path.is_file():
                pending.append(
                    f"kitctl vendor {candidate['id']} --path {config['path']} "
                    f"--pin {report['sha']} --kind {kind} --name {name}"
                )
                continue
            vendored = dict(load_yaml(meta_path))
            fills = sorted(candidate.get("fills", []) or [])
            if fills and vendored.get("provides") != fills:
                vendored["provides"] = fills
                vendored["hash"] = content_hash(vendored, meta_path.with_suffix(""))
                meta_path.write_text(_dump(vendored), encoding="utf-8")
                written.append(meta_path.relative_to(warehouse).as_posix())
        elif not meta_path.exists():
            metadata: dict[str, Any] = {
                "schema_version": 2,
                "kind": kind,
                "name": name,
                "description": str(config.get("description", "")),
                "source": {"url": report["source"], "license": report["license"]},
                "provides": sorted(candidate.get("fills", []) or []),
            }
            metadata.update({k: v for k, v in config.items() if k in PASSTHROUGH[kind]})
            metadata["hash"] = content_hash(metadata, None)
            meta_path.parent.mkdir(parents=True, exist_ok=True)
            meta_path.write_text(_dump(metadata), encoding="utf-8")
            written.append(meta_path.relative_to(warehouse).as_posix())
        if kind == "mcp":
            loadout.setdefault("mcp", {})[name.rsplit("/", 1)[-1]] = {"primitive": ref}
        else:
            loadout.setdefault(LIST_FOR_KIND[kind], []).append(ref)
        for slot in candidate.get("fills", []) or []:
            if slot in brief.get("required_slots", []):
                slots[slot] = ref
    if slots:
        loadout["slots"] = dict(sorted(slots.items()))
    unfilled = [s for s in brief.get("required_slots", []) if s not in slots]
    draft = inbox / "loadouts" / f"{slug}.yaml"
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text(_dump(loadout), encoding="utf-8")
    written.append(draft.relative_to(warehouse).as_posix())

    lines = [
        f"# Gaps for {slug}",
        "",
        f"Proposed {dt.date.today().isoformat()} from "
        f"{len(candidates)} candidates; draft loadout: `inbox/loadouts/{slug}.yaml`.",
        "",
    ]
    lines += ["## Unfilled slots", ""] + ([f"- `{s}`" for s in unfilled] or ["- none"]) + [""]
    lines += (
        ["## Excluded candidates", ""]
        + ([f"- `{ident}`: {'; '.join(reasons)}" for ident, reasons in excluded] or ["- none"])
        + [""]
    )
    lines += ["## Vendor before review", ""] + ([f"- `{c}`" for c in pending] or ["- none"])
    lines += (
        ["", "## Borrow ideas (build tasks)", ""]
        + ([f"- `{c['id']}`: {c.get('notes', 'see evidence')}" for c in ideas] or ["- none"])
        + [""]
    )
    gaps = forge_dir(warehouse, slug) / "gaps.md"
    gaps.write_text("\n".join(lines), encoding="utf-8")
    written.append(gaps.relative_to(warehouse).as_posix())
    return {
        "loadout": loadout,
        "written": written,
        "unfilled": unfilled,
        "excluded": dict(excluded),
        "pending_vendor": pending,
        "ideas": [c["id"] for c in ideas],
    }


# Verify ------------------------------------------------------------------------------------------


def _resolve_cwd(spec: str | None, warehouse: Path, cache: Path) -> Path:
    if not spec:
        return warehouse
    if spec.startswith("quarantine:"):
        clone, _ = find_scouted(spec.split(":", 1)[1], cache)
        return clone
    if spec.startswith("inbox:"):
        relative = spec.split(":", 1)[1]
        if is_traversal(relative):
            raise ForgeError([f"eval cwd escapes the inbox: {spec}"])
        return warehouse / "inbox" / PurePosixPath(relative)
    return Path(spec).expanduser()


def _expand(args: list[str], cwd: Path) -> list[str]:
    """Expand glob arguments relative to cwd ourselves, since no shell runs."""
    result: list[str] = []
    for arg in args:
        if any(ch in arg for ch in "*?[") and not arg.startswith("-") and not is_traversal(arg):
            matches = sorted(p.relative_to(cwd).as_posix() for p in cwd.glob(arg))
            result += matches or [arg]
        else:
            result.append(arg)
    return result


def verify(
    warehouse: Path, slug: str, cache: Path, with_plugin_eval: bool = False
) -> dict[str, Any]:
    """Run the brief's evals plus plugin validation for inbox plugins. Records results."""
    brief = load_brief(warehouse, slug)
    results: list[dict[str, Any]] = []
    jobs: list[dict[str, Any]] = [dict(item) for item in brief.get("evals", [])]
    claude = shutil.which("claude")
    inbox = warehouse / "inbox"
    if any((inbox / "skills").glob("*/SKILL.md")):
        # `claude plugin validate` checks skills/, agents/ and commands/ under a directory.
        jobs.append(
            {
                "name": "claude plugin validate --strict inbox (skills)",
                "run": ["claude", "plugin", "validate", "--strict", str(inbox)],
                "cwd": str(inbox),
                "requires": "claude",
            }
        )
    plugins_dir = inbox / "plugins"
    for manifest in sorted(plugins_dir.glob("*/.claude-plugin/plugin.json")):
        root = manifest.parent.parent
        jobs.append(
            {
                "name": f"claude plugin validate --strict {root.name}",
                "run": ["claude", "plugin", "validate", "--strict", str(root)],
                "cwd": str(root),
                "requires": "claude",
            }
        )
        if with_plugin_eval and (root / "evals").is_dir():
            jobs.append(
                {
                    "name": f"claude plugin eval {root.name}",
                    "run": ["claude", "plugin", "eval", str(root)],
                    "cwd": str(root),
                    "requires": "claude",
                }
            )
    for job in jobs:
        if job.get("requires") == "claude" and claude is None:
            results.append(
                {"name": job["name"], "status": "skipped", "detail": "claude is not installed"}
            )
            continue
        cwd = _resolve_cwd(job.get("cwd"), warehouse, cache)
        command = _expand(job["run"], cwd)
        binary = shutil.which(command[0])
        if binary is None:
            results.append(
                {"name": job["name"], "status": "failed", "detail": f"{command[0]} not found"}
            )
            continue
        try:
            done = subprocess.run(
                [binary, *command[1:]],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=int(job.get("timeout", 600)),
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            results.append(
                {"name": job["name"], "status": "failed", "detail": type(error).__name__}
            )
            continue
        output = (done.stdout + done.stderr).strip()
        status = "passed" if done.returncode == 0 else "failed"
        if status == "passed" and job.get("expect") and not re.search(job["expect"], output):
            status = "failed"
        results.append(
            {
                "name": job["name"],
                "status": status,
                "exit": done.returncode,
                "tail": output.splitlines()[-5:],
            }
        )
    record = {
        "slug": slug,
        "verified": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "results": results,
        "ok": all(r["status"] != "failed" for r in results) and bool(results),
    }
    (forge_dir(warehouse, slug) / "verify.json").write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )
    return record
