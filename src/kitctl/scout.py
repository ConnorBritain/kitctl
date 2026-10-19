"""Quarantine a candidate repository and report facts about it. Never executes it.

`scout` shallow-clones into ~/.cache/kitctl/quarantine/<host>/<owner>/<repo>@<sha>
with hooks, filters, submodules and LFS smudging disabled, then only reads
files. Every fact carries its evidence as file:line. `vendor` copies one pinned
path from a quarantined clone into the warehouse inbox, with provenance, a
content hash and the license text, and refuses sources the license policy
does not allow.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from ruamel.yaml import YAML

from kitctl.model import (
    KIND_DIRS,
    NAME_RE,
    ModelError,
    content_hash,
    is_traversal,
)
from kitctl.native import redact_url

FACTS_VERSION = 1
MAX_BYTES = 1_000_000
SKIP_DIRS = {".git", "node_modules", ".venv", "dist", "build", "__pycache__"}
DEFAULT_ALLOW = [
    "MIT",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "0BSD",
    "Unlicense",
    "CC0-1.0",
    "MPL-2.0",
]

LICENSE_TEXT = [
    ("Apache-2.0", re.compile(r"Apache License,?\s+Version 2\.0", re.I)),
    ("MIT", re.compile(r"Permission is hereby granted, free of charge", re.I)),
    ("BSD-3-Clause", re.compile(r"Neither the name of .* nor the names of its", re.I | re.S)),
    ("BSD-2-Clause", re.compile(r"Redistribution and use in source and binary forms", re.I)),
    ("ISC", re.compile(r"Permission to use, copy, modify, and/or distribute this software", re.I)),
    ("MPL-2.0", re.compile(r"Mozilla Public License,?\s+(v\.|Version)\s*2\.0", re.I)),
    ("AGPL-3.0", re.compile(r"GNU AFFERO GENERAL PUBLIC LICENSE", re.I)),
    ("GPL-3.0", re.compile(r"GNU GENERAL PUBLIC LICENSE\s+Version 3", re.I)),
    ("Unlicense", re.compile(r"This is free and unencumbered software released into the public")),
]
TELEMETRY = re.compile(
    r"posthog|segment\.(io|com)|mixpanel|amplitude|google-analytics|googletagmanager|"
    r"\bsentry\b|usage[ _-]statistics|telemetry|analytics|crash[ _-]?report",
    re.I,
)
OUTBOUND = re.compile(r"""\b(fetch|axios\.\w+|requests\.(get|post)|urlopen)\(\s*['"`]https?://""")
CURL_PIPE = re.compile(
    r"\b(curl|wget)\b[^\n|]*\|\s*(sudo\s+)?(ba|z)?sh\b|\biwr\b[^\n|]*\|\s*iex", re.I
)
SELF_FETCH = re.compile(
    r"(fetch|download|pull|get)\s+(the\s+)?latest\s+(rules|guidelines|instructions|version)|"
    r"raw\.githubusercontent\.com",
    re.I,
)
AGGRESSIVE = re.compile(r"\bMANDATORY\b|\balways use\b|\bexecute first\b|\bMUST be used\b", re.I)
INSTALL_SCRIPTS = ("preinstall", "install", "postinstall", "prepare")


class ScoutError(Exception):
    pass


def cache_root(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return explicit
    if os.environ.get("KITCTL_CACHE"):
        return Path(os.environ["KITCTL_CACHE"])
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "kitctl"


def _git(args: list[str], cwd: Path | None = None) -> str:
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_LFS_SKIP_SMUDGE": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    safe = [
        "-c",
        "core.hooksPath=" + os.devnull,
        "-c",
        "core.fsmonitor=false",
        "-c",
        "protocol.file.allow=always",
        "-c",
        "filter.lfs.smudge=",
        "-c",
        "filter.lfs.process=",
        "-c",
        "filter.lfs.required=false",
        "-c",
        "submodule.recurse=false",
    ]
    done = subprocess.run(
        ["git", *safe, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=600,
    )
    if done.returncode != 0:
        raise ScoutError(f"git {args[0]} failed: {done.stderr.strip().splitlines()[-1:]}")
    return done.stdout.strip()


def source_parts(source: str) -> tuple[str, str, str]:
    """host, owner, repo for a URL, scp-style address, or local path."""
    if re.match(r"^[a-z][a-z0-9+.-]*://", source):
        rest = source.split("://", 1)[1].split("@")[-1]
        host, _, path = rest.partition("/")
    elif re.match(r"^[^/\\]+@[^:]+:", source):
        host, _, path = source.split("@", 1)[1].partition(":")
    else:
        path = Path(source).expanduser().resolve().as_posix()
        host = "local"
    segments = [s for s in path.removesuffix(".git").strip("/").split("/") if s]
    if not segments:
        raise ScoutError(f"cannot name repository from {source!r}")
    repo = segments[-1]
    owner = segments[-2] if len(segments) > 1 else "_"
    clean = [re.sub(r"[^A-Za-z0-9._-]", "_", part) for part in (host.split(":")[0], owner, repo)]
    return clean[0], clean[1], clean[2]


def quarantine(source: str, cache: Path) -> tuple[Path, str]:
    """Shallow clone into quarantine, pinned by commit. Returns (clone dir, sha)."""
    host, owner, repo = source_parts(source)
    base = cache / "quarantine" / host / owner
    base.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{repo}-", dir=base))
    try:
        url = source if host != "local" else Path(source).expanduser().resolve().as_uri()
        _git(
            [
                "clone",
                "--quiet",
                "--depth",
                "1",
                "--no-tags",
                "--single-branch",
                "--no-recurse-submodules",
                url,
                str(staging / "tree"),
            ]
        )
        sha = _git(["rev-parse", "HEAD"], staging / "tree")
        target = base / f"{repo}@{sha}"
        if target.exists():
            shutil.rmtree(staging, ignore_errors=True)
            return target, sha
        (staging / "tree").rename(target)
        return target, sha
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _files(root: Path) -> list[Path]:
    found: list[Path] = []
    for current, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(names):
            path = Path(current) / name
            if not path.is_symlink() and path.is_file() and path.stat().st_size <= MAX_BYTES:
                found.append(path)
    return found


def _text(path: Path) -> str | None:
    data = path.read_bytes()
    if b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace")


def _evidence(root: Path, path: Path, line: int, text: str) -> dict[str, Any]:
    return {"file": path.relative_to(root).as_posix(), "line": line, "text": text.strip()[:160]}


def _frontmatter(text: str) -> dict[str, Any]:
    match = re.match(r"^---\r?\n(.*?)\r?\n---", text, re.S)
    if not match:
        return {}
    try:
        data = YAML(typ="safe", pure=True).load(match.group(1))
    except Exception:  # malformed frontmatter is a fact, not a crash
        return {"_error": "unparseable frontmatter"}
    return data if isinstance(data, dict) else {}


def facts(root: Path) -> dict[str, Any]:
    """Read-only facts about a checkout, each with file:line evidence."""
    licenses: list[dict[str, Any]] = []
    manifests: list[dict[str, Any]] = []
    findings: dict[str, list[dict[str, Any]]] = {
        "install_scripts": [],
        "telemetry": [],
        "outbound_network": [],
        "self_fetching": [],
        "aggressive_triggers": [],
    }
    for path in _files(root):
        relative = path.relative_to(root).as_posix()
        text = _text(path)
        if text is None:
            continue
        lines = text.splitlines()
        name = path.name
        if re.match(r"^(LICEN[CS]E|COPYING)(\.|$)", name, re.I) and "/" not in relative:
            for spdx, pattern in LICENSE_TEXT:
                match = pattern.search(text)
                if match:
                    line = text.count("\n", 0, match.start()) + 1
                    licenses.append(
                        {
                            "spdx": spdx,
                            "source": "license-file",
                            "evidence": _evidence(root, path, line, lines[line - 1]),
                        }
                    )
                    break
            else:
                licenses.append(
                    {
                        "spdx": "NOASSERTION",
                        "source": "license-file",
                        "evidence": _evidence(root, path, 1, lines[0] if lines else ""),
                    }
                )
        if name == "package.json":
            try:
                package = json.loads(text)
            except ValueError:
                package = {}
            if isinstance(package, dict):
                if isinstance(package.get("license"), str) and "/" not in relative:
                    line = next(
                        (i for i, text_line in enumerate(lines, 1) if '"license"' in text_line), 1
                    )
                    licenses.append(
                        {
                            "spdx": package["license"],
                            "source": "package.json",
                            "evidence": _evidence(root, path, line, lines[line - 1]),
                        }
                    )
                scripts = package.get("scripts", {})
                for hook in INSTALL_SCRIPTS:
                    if isinstance(scripts, dict) and hook in scripts:
                        line = next(
                            (i for i, text_line in enumerate(lines, 1) if f'"{hook}"' in text_line),
                            1,
                        )
                        findings["install_scripts"].append(
                            {
                                "detail": f"npm {hook}: {scripts[hook]}",
                                "evidence": _evidence(root, path, line, lines[line - 1]),
                            }
                        )
        if name == "pyproject.toml" and "/" not in relative:
            match = re.search(r'^license\s*=\s*(?:"([^"]+)"|\{\s*text\s*=\s*"([^"]+)")', text, re.M)
            if match:
                line = text.count("\n", 0, match.start()) + 1
                licenses.append(
                    {
                        "spdx": match.group(1) or match.group(2),
                        "source": "pyproject",
                        "evidence": _evidence(root, path, line, lines[line - 1]),
                    }
                )
        kind = None
        if name == "SKILL.md":
            kind = "skill"
        elif relative.endswith(".claude-plugin/plugin.json"):
            kind = "claude-plugin"
        elif relative.endswith(".codex-plugin/plugin.json"):
            kind = "codex-plugin"
        elif name == "marketplace.json":
            kind = "marketplace"
        elif name == ".mcp.json":
            kind = "mcp"
        elif name == "hooks.json":
            kind = "hooks"
        elif re.search(r"(^|/)agents/[^/]+\.md$", relative):
            kind = "agent"
        if kind:
            manifest: dict[str, Any] = {"kind": kind, "file": relative}
            if kind in {"skill", "agent"}:
                front = _frontmatter(text)
                manifest.update(
                    {k: front[k] for k in ("name", "description", "_error") if k in front}
                )
                description = str(front.get("description", ""))
                for index, raw in enumerate(lines, 1):
                    if description and description[:40] in raw:
                        for match in AGGRESSIVE.finditer(description):
                            findings["aggressive_triggers"].append(
                                {
                                    "detail": match.group(0),
                                    "evidence": _evidence(root, path, index, raw),
                                }
                            )
                        break
                for index, raw in enumerate(lines, 1):
                    if SELF_FETCH.search(raw):
                        findings["self_fetching"].append(
                            {
                                "detail": "skill fetches instructions at runtime",
                                "evidence": _evidence(root, path, index, raw),
                            }
                        )
            manifests.append(manifest)
        for index, raw in enumerate(lines, 1):
            if CURL_PIPE.search(raw):
                findings["install_scripts"].append(
                    {
                        "detail": "pipes a download into a shell",
                        "evidence": _evidence(root, path, index, raw),
                    }
                )
            telemetry = TELEMETRY.search(raw)
            if telemetry:
                findings["telemetry"].append(
                    {
                        "detail": telemetry.group(0),
                        "evidence": _evidence(root, path, index, raw),
                    }
                )
            if OUTBOUND.search(raw):
                findings["outbound_network"].append(
                    {"detail": "calls a remote URL", "evidence": _evidence(root, path, index, raw)}
                )
    found_spdx = sorted({item["spdx"] for item in licenses if item["spdx"] != "NOASSERTION"})
    return {
        "license": found_spdx[0]
        if len(found_spdx) == 1
        else ("NOASSERTION" if not found_spdx else "CONFLICT"),
        "licenses": licenses,
        "manifests": manifests,
        **findings,
    }


def scout(source: str, cache: Path) -> dict[str, Any]:
    clone, sha = quarantine(source, cache)
    host, owner, repo = source_parts(source)
    report = {
        "facts_version": FACTS_VERSION,
        "id": f"{host}/{owner}/{repo}@{sha}",
        "source": redact_url(source) if "://" in source else source,
        "sha": sha,
        "last_commit": _git(["log", "-1", "--format=%cI"], clone),
        "quarantine": str(clone),
        "scouted": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **facts(clone),
    }
    (clone.parent / f"{clone.name}.facts.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    return report


# Vendor ------------------------------------------------------------------------------------------


def load_policy(warehouse: Path, explicit: Path | None = None) -> dict[str, list[str]]:
    """License policy: an explicit file (a forge brief), else warehouse policy.yaml, else a
    permissive default."""
    path = explicit or warehouse / "policy.yaml"
    if path.is_file():
        data = YAML(typ="safe", pure=True).load(path.read_text(encoding="utf-8")) or {}
        licenses = (
            data.get("licenses", data.get("license_policy", {})) if isinstance(data, dict) else {}
        )
        allow = (
            licenses.get("allow", DEFAULT_ALLOW) if isinstance(licenses, dict) else DEFAULT_ALLOW
        )
        deny = licenses.get("deny", []) if isinstance(licenses, dict) else []
        return {"allow": [str(x) for x in allow], "deny": [str(x) for x in deny]}
    return {"allow": list(DEFAULT_ALLOW), "deny": []}


def find_scouted(scouted_id: str, cache: Path) -> tuple[Path, dict[str, Any]]:
    if is_traversal(scouted_id.replace("@", "_")) or scouted_id.count("/") != 2:
        raise ScoutError(f"invalid scouted id {scouted_id!r} (expected host/owner/repo@sha)")
    clone = cache / "quarantine" / PurePosixPath(scouted_id)
    report_path = clone.parent / f"{clone.name}.facts.json"
    if not clone.is_dir() or not report_path.is_file():
        raise ScoutError(f"{scouted_id} is not in quarantine; run `kitctl scout` first")
    return clone, json.loads(report_path.read_text(encoding="utf-8"))


def vendor(
    scouted_id: str,
    path: str,
    warehouse: Path,
    cache: Path,
    *,
    kind: str = "skill",
    name: str | None = None,
    pin: str | None = None,
    policy_file: Path | None = None,
) -> dict[str, Any]:
    """Copy one path from a quarantined clone into inbox/, or refuse."""
    clone, report = find_scouted(scouted_id, cache)
    if pin and not report["sha"].startswith(pin):
        raise ScoutError(f"pin {pin} does not match the quarantined commit {report['sha']}")
    if kind not in KIND_DIRS:
        raise ScoutError(f"unknown kind {kind!r}")
    if is_traversal(path):
        raise ScoutError(f"path escapes the clone: {path!r}")
    source = (clone / PurePosixPath(path)).resolve()
    if not source.is_relative_to(clone.resolve()) or not source.exists():
        raise ScoutError(f"{path} is not inside {scouted_id}")
    license_id = report["license"]
    policy = load_policy(warehouse, policy_file)
    if license_id in {"NOASSERTION", "CONFLICT"}:
        raise ScoutError(f"{scouted_id} has no clear license ({license_id}); refusing to vendor")
    if license_id in policy["deny"] or license_id not in policy["allow"]:
        raise ScoutError(f"license {license_id} is not allowed by policy; refusing to vendor")
    name = name or (source.name if source.is_dir() else source.stem)
    if not NAME_RE.match(name):
        raise ScoutError(f"invalid primitive name {name!r}")
    inbox = warehouse / "inbox" / KIND_DIRS[kind]
    meta_path = inbox / f"{name}.yaml"
    assets = inbox / name
    if meta_path.exists() or assets.exists():
        raise ScoutError(f"{meta_path.relative_to(warehouse)} already exists in the inbox")
    links = [p for p in ([source] + list(source.rglob("*"))) if p.is_symlink()]
    if links:
        raise ScoutError(f"refusing to vendor symlinks: {links[0].relative_to(clone)}")
    assets.mkdir(parents=True)
    try:
        if source.is_dir():
            shutil.copytree(
                source, assets, dirs_exist_ok=True, ignore=shutil.ignore_patterns(*SKIP_DIRS)
            )
        else:
            shutil.copy2(source, assets / source.name)
        license_files = [
            p
            for p in sorted(clone.iterdir())
            if re.match(r"^(LICEN[CS]E|COPYING|NOTICE)(\.|$)", p.name, re.I)
            and p.is_file()
            and not p.is_symlink()
        ]
        notice = [
            f"# Third-party notices\n\n## {report['source']} @ {report['sha']}\n\n"
            f"Path: `{path}`. License: {license_id}.\n"
        ]
        for file in license_files:
            body = file.read_text(encoding="utf-8", errors="replace").rstrip()
            notice.append(f"\n### {file.name}\n\n```\n{body}\n```\n")
        notices = assets / "THIRD_PARTY_NOTICES.md"
        existing = notices.read_text(encoding="utf-8") + "\n" if notices.exists() else ""
        notices.write_text(existing + "".join(notice), encoding="utf-8")
        description = ""
        skill = assets / "SKILL.md"
        if skill.is_file():
            description = str(
                _frontmatter(skill.read_text(encoding="utf-8")).get("description", "")
            )
        metadata: dict[str, Any] = {
            "schema_version": 2,
            "kind": kind,
            "name": name,
            "description": description,
            "source": {
                "git": report["source"],
                "sha": report["sha"],
                "path": path,
                "license": license_id,
                "retrieved": report["scouted"][:10],
            },
        }
        metadata["hash"] = content_hash(metadata, assets)
        yaml = YAML()
        yaml.default_flow_style = False
        with meta_path.open("w", encoding="utf-8") as stream:
            yaml.dump(metadata, stream)
    except (OSError, ModelError):
        shutil.rmtree(assets, ignore_errors=True)
        meta_path.unlink(missing_ok=True)
        raise
    return {
        "primitive": f"{kind}:{name}",
        "metadata": meta_path.relative_to(warehouse).as_posix(),
        "assets": assets.relative_to(warehouse).as_posix(),
        "hash": metadata["hash"],
        "license": license_id,
    }
