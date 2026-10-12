"""Combine inventory, usage, and analysis into one report; render JSON, Markdown, HTML."""

from __future__ import annotations

import html
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kitctl import analysis
from kitctl import config as kit_config
from kitctl import paths as kit_paths
from kitctl.inventory import inventory
from kitctl.usage.run import mine

FORMATS = ("md", "json", "html")


def build(
    roots: list[Path] | None = None,
    since: str | None = None,
    use_cache: bool = True,
    include_worktrees: bool | None = None,
) -> dict[str, Any]:
    paths = kit_paths.resolve()
    settings = kit_config.load(paths.kit_config_file)
    report = inventory(
        roots, use_cache=use_cache, settings=settings, include_worktrees=include_worktrees
    )
    repos = [Path(repo) for repo in report["roots"]["repos"]]
    usage = mine(paths, repos, since=since, use_cache=use_cache)
    stale_days = settings.stale_days if settings else 60
    result = analysis.analyze(report["entries"], usage["records"], stale_days)
    per_entry = result.pop("usage_by_entry")
    report["findings"].extend(result.pop("findings"))
    for adapter, stats in usage["adapters"].items():
        if stats["unknown_shape_total"]:
            report["findings"].append(
                {
                    "severity": "info",
                    "code": "usage-unknown-shape",
                    "path": "",
                    "message": f"{stats['unknown_shape_total']} {adapter} log lines had an "
                    "unrecognized shape and were skipped",
                }
            )
    report["usage"] = usage
    report["analysis"] = result
    report["matrix"] = matrix(report["entries"], per_entry)
    return report


def _column(entry: dict[str, Any]) -> str:
    if entry["repo"]:
        return Path(entry["repo"]).name
    if entry["provided_by"]:
        provider = entry["provided_by"]
        return f"plugin:{provider}" if "@" in provider else provider
    return str(entry["scope"])


def matrix(entries: list[dict[str, Any]], per_entry: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per (kind, harness, name); locations, usage, and status merged."""
    rows: dict[tuple[str, str, str], dict[str, Any]] = {}
    for entry, usage in zip(entries, per_entry, strict=True):
        if entry["kind"] in {"link", "settings"}:
            continue
        key = (entry["kind"], entry["harness"], entry["name"])
        row = rows.setdefault(
            key,
            {
                "kind": entry["kind"],
                "harness": entry["harness"],
                "name": entry["name"],
                "locations": [],
                "calls": 0,
                "last_used": "",
                "status": usage["status"],
                "repos_used": [],
                "_records": {},
            },
        )
        row["locations"].append(
            {
                "column": _column(entry),
                "scope": entry["scope"],
                "hash": (entry["content_hash"] or "")[:12],
                "path": entry["path"],
            }
        )
        row["_records"].update(usage["records"])
        row["calls"] = sum(row["_records"].values())
        row["last_used"] = max(row["last_used"], usage["last_used"])
        row["repos_used"] = sorted(set(row["repos_used"]) | set(usage["repos"]))
        rank = {"active": 3, "stale": 2, "unused": 1, "n/a": 0}
        if rank[usage["status"]] > rank[row["status"]]:
            row["status"] = usage["status"]
    for row in rows.values():
        del row["_records"]
    return sorted(rows.values(), key=lambda r: (r["kind"], r["harness"], r["name"]))


def default_path(fmt: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return kit_paths.resolve().report_dir / f"report-{stamp}.{fmt}"


# -- rendering ---------------------------------------------------------------


def render(report: dict[str, Any], fmt: str) -> str:
    if fmt == "json":
        return json.dumps(report, indent=2) + "\n"
    if fmt == "md":
        return render_markdown(report)
    if fmt == "html":
        return render_html(report)
    raise ValueError(f"Unknown format: {fmt}")


def _summary(report: dict[str, Any]) -> list[tuple[str, str]]:
    kinds = Counter(e["kind"] for e in report["entries"] if e["kind"] != "link")
    status = Counter(row["status"] for row in report["matrix"])
    severities = Counter(f["severity"] for f in report["findings"])
    result = report["analysis"]
    return [
        ("Generated", f"{report['generated_by']} at {datetime.now(UTC):%Y-%m-%d %H:%M} UTC"),
        ("Repositories", str(len(report["roots"]["repos"]))),
        ("Items", ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) or "none"),
        ("Usage status", ", ".join(f"{n} {s}" for s, n in sorted(status.items())) or "none"),
        ("Stale after", f"{result['stale_days']} days"),
        (
            "Findings",
            ", ".join(f"{severities[s]} {s}" for s in ("error", "warning", "info") if severities[s])
            or "none",
        ),
        (
            "Analysis",
            f"{len(result['duplicates'])} duplicate groups, {len(result['drift'])} drifted, "
            f"{len(result['shadowing'])} shadowed, {len(result['overlap'])} MCP overlaps",
        ),
        (
            "Usage outside inventory",
            f"{sum(u['calls'] for u in result['unmatched_usage'])} calls to "
            f"{len(result['unmatched_usage'])} names not in the current inventory "
            "(removed, built-in, or claude.ai connectors)",
        ),
    ]


def _md_escape(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _locations_text(row: dict[str, Any]) -> str:
    return "; ".join(
        f"{loc['column']} ({loc['scope']}{', ' + loc['hash'] if loc['hash'] else ''})"
        for loc in row["locations"]
    )


def render_markdown(report: dict[str, Any]) -> str:
    out = ["# kitctl report", ""]
    out += [f"- **{label}:** {_md_escape(value)}" for label, value in _summary(report)]
    out += ["", "kitctl is read-only toward agent configuration; nothing was changed.", ""]
    out += ["## Findings", ""]
    by_severity: dict[str, list[dict[str, str]]] = defaultdict(list)
    for finding in report["findings"]:
        by_severity[finding["severity"]].append(finding)
    if not report["findings"]:
        out.append("None.")
    for severity in ("error", "warning", "info"):
        if by_severity[severity]:
            out += [f"### {severity.title()} ({len(by_severity[severity])})", ""]
            out += [
                f"- `{f['code']}` {_md_escape(f['message'])}"
                + (f" — `{_md_escape(f['path'])}`" if f["path"] else "")
                for f in by_severity[severity]
            ]
            out.append("")
    out += ["## Inventory matrix", ""]
    out += [
        "| Kind | Harness | Name | Locations | Calls | Last used | Status |",
        "|---|---|---|---|---:|---|---|",
    ]
    for row in report["matrix"]:
        out.append(
            f"| {row['kind']} | {row['harness']} | {_md_escape(row['name'])} | "
            f"{_md_escape(_locations_text(row))} | {row['calls']} | {row['last_used'][:10]} | "
            f"{row['status']} |"
        )
    result = report["analysis"]
    out += ["", "## Duplicates (identical content in several places)", ""]
    out += [
        f"- {d['kind']} {', '.join(d['names'])}: "
        + "; ".join(_md_escape(loc["path"]) for loc in d["locations"])
        for d in result["duplicates"]
    ] or ["None."]
    out += ["", "## Drift (same name, different content)", ""]
    for item in result["drift"]:
        out.append(f"- {item['kind']} **{_md_escape(item['name'])}**")
        out += [
            f"  - `{v['hash'][:12]}`: "
            + "; ".join(_md_escape(loc["path"]) for loc in v["locations"])
            for v in item["variants"]
        ]
    if not result["drift"]:
        out.append("None.")
    out += ["", "## Shadowing (same name at several scopes)", ""]
    out += [
        f"- {s['harness']} {s['kind']} **{_md_escape(s['name'])}**"
        + (f" in {Path(s['repo']).name}" if s["repo"] else "")
        + f": {s['winner_scope']} wins over "
        + ", ".join(sorted({loc["scope"] for loc in s["locations"]} - {s["winner_scope"]}))
        for s in result["shadowing"]
    ] or ["None."]
    out += ["", "## MCP overlap (same command or host, different names)", ""]
    out += [
        f"- {o['key']}: "
        + ", ".join(f"{s['name']} ({s['harness']}/{s['scope']})" for s in o["servers"])
        for o in result["overlap"]
    ] or ["None."]
    out += ["", "## Used but not in the current inventory", ""]
    out += ["Removed items, harness built-ins, or claude.ai connectors still get used:", ""]
    out += [
        f"- {u['harness']} {u['kind']} **{_md_escape(u['name'])}**: {u['calls']} calls, "
        f"last {u['last_used'][:10]}" + (f" ({Path(u['repo']).name})" if u["repo"] else "")
        for u in result["unmatched_usage"][:50]
    ] or ["None."]
    out += ["", "## Usage sources", ""]
    for adapter, stats in report["usage"]["adapters"].items():
        out.append(
            f"- {adapter}: {stats['files']} log files, {stats['files_read']} read this run "
            f"({stats['bytes_read']} bytes), {stats['malformed_total']} malformed and "
            f"{stats['unknown_shape_total']} unrecognized lines skipped"
        )
    return "\n".join(out) + "\n"


_CSS = """
:root{--bg:#fff;--fg:#1a1a1a;--muted:#666;--line:#e3e3e3;--chip:#f3f3f3;--err:#b00020;
--warn:#8a5a00;--ok:#1b6e3a;--accent:#2457c5}
@media (prefers-color-scheme:dark){:root{--bg:#141414;--fg:#ececec;--muted:#a0a0a0;
--line:#2c2c2c;--chip:#222;--err:#ff6b81;--warn:#e0a84a;--ok:#5fc489;--accent:#7aa2ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}main{max-width:1200px;margin:0 auto;
padding:24px 16px}h1{font-size:22px;margin:0 0 12px}h2{font-size:17px;margin:28px 0 8px}
dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 16px;margin:0}dt{color:var(--muted)}
dd{margin:0}.note{color:var(--muted);margin:8px 0 0}table{border-collapse:collapse;width:100%}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{cursor:pointer;user-select:none;white-space:nowrap}td.num{text-align:right}
.wrap{overflow-x:auto}.chip{display:inline-block;background:var(--chip);border-radius:4px;
padding:0 6px;margin:1px 2px;font-size:12px;white-space:nowrap}
.error{color:var(--err)}.warning{color:var(--warn)}.info{color:var(--muted)}
.active{color:var(--ok)}.stale{color:var(--warn)}.unused{color:var(--err)}
input{font:inherit;padding:6px 8px;width:100%;max-width:360px;background:var(--bg);color:var(--fg);
border:1px solid var(--line);border-radius:4px;margin:4px 0 8px}code{font-size:12px}
"""

_JS = """
document.querySelectorAll('table.sortable').forEach(function(t){
 t.querySelectorAll('th').forEach(function(th,i){th.addEventListener('click',function(){
  var rows=Array.from(t.tBodies[0].rows),dir=th.dataset.dir==='asc'?-1:1;
  th.dataset.dir=dir>0?'asc':'desc';
  rows.sort(function(a,b){var x=a.cells[i].dataset.v||a.cells[i].textContent,
   y=b.cells[i].dataset.v||b.cells[i].textContent,nx=parseFloat(x),ny=parseFloat(y);
   return (!isNaN(nx)&&!isNaN(ny)?nx-ny:x.localeCompare(y))*dir;});
  rows.forEach(function(r){t.tBodies[0].appendChild(r);});});});});
var f=document.getElementById('filter');if(f){f.addEventListener('input',function(){
 var q=f.value.toLowerCase();document.querySelectorAll('#matrix tbody tr').forEach(function(r){
  r.style.display=r.textContent.toLowerCase().indexOf(q)<0?'none':'';});});}
"""


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def render_html(report: dict[str, Any]) -> str:
    parts = [
        "<!doctype html><html lang=en><head><meta charset=utf-8>",
        "<meta name=viewport content='width=device-width,initial-scale=1'>",
        "<title>kitctl report</title><style>",
        _CSS,
        "</style></head><body><main>",
        "<h1>kitctl report</h1><dl>",
    ]
    parts += [f"<dt>{_e(label)}</dt><dd>{_e(value)}</dd>" for label, value in _summary(report)]
    parts.append("</dl><p class=note>Read-only: no agent configuration was changed.</p>")
    parts.append("<h2>Findings</h2>")
    if report["findings"]:
        parts.append(
            "<div class=wrap><table class=sortable><thead><tr><th>Severity</th>"
            "<th>Code</th><th>Message</th><th>Path</th></tr></thead><tbody>"
        )
        order = {"error": 0, "warning": 1, "info": 2}
        for finding in sorted(report["findings"], key=lambda f: order[f["severity"]]):
            parts.append(
                f"<tr><td class={_e(finding['severity'])} data-v={order[finding['severity']]}>"
                f"{_e(finding['severity'])}</td><td><code>{_e(finding['code'])}</code></td>"
                f"<td>{_e(finding['message'])}</td><td><code>{_e(finding['path'])}</code></td></tr>"
            )
        parts.append("</tbody></table></div>")
    else:
        parts.append("<p>None.</p>")
    parts.append("<h2>Inventory matrix</h2><input id=filter placeholder='Filter rows'>")
    parts.append(
        "<div class=wrap><table id=matrix class=sortable><thead><tr><th>Kind</th>"
        "<th>Harness</th><th>Name</th><th>Locations</th><th>Calls</th>"
        "<th>Last used</th><th>Status</th></tr></thead><tbody>"
    )
    for row in report["matrix"]:
        chips = "".join(
            f"<span class=chip title='{_e(loc['path'])}'>{_e(loc['column'])} · {_e(loc['scope'])}"
            + (f" · {_e(loc['hash'][:8])}" if loc["hash"] else "")
            + "</span>"
            for loc in row["locations"]
        )
        parts.append(
            f"<tr><td>{_e(row['kind'])}</td><td>{_e(row['harness'])}</td><td>{_e(row['name'])}</td>"
            f"<td>{chips}</td><td class=num>{row['calls']}</td>"
            f"<td>{_e(row['last_used'][:10])}</td><td class={_e(row['status'].replace('/', ''))}>"
            f"{_e(row['status'])}</td></tr>"
        )
    parts.append("</tbody></table></div>")
    result = report["analysis"]
    sections = [
        (
            "Duplicates",
            [
                f"{d['kind']} {', '.join(d['names'])}: "
                + "; ".join(loc["path"] for loc in d["locations"])
                for d in result["duplicates"]
            ],
        ),
        (
            "Used but not in inventory",
            [
                f"{u['harness']} {u['kind']} {u['name']}: {u['calls']} calls"
                for u in result["unmatched_usage"][:50]
            ],
        ),
        (
            "Drift",
            [
                f"{d['kind']} {d['name']}"
                + (" (Claude vs Codex)" if d["cross_harness"] else "")
                + ": "
                + " vs ".join(f"{v['hash'][:12]} ({len(v['locations'])})" for v in d["variants"])
                for d in result["drift"]
            ],
        ),
        (
            "Shadowing",
            [
                f"{s['harness']} {s['kind']} {s['name']}"
                + (f" in {Path(s['repo']).name}" if s["repo"] else "")
                + f": {s['winner_scope']} wins"
                for s in result["shadowing"]
            ],
        ),
        (
            "MCP overlap",
            [
                f"{o['key']}: " + ", ".join(s["name"] for s in o["servers"])
                for o in result["overlap"]
            ],
        ),
    ]
    for title, items in sections:
        parts.append(f"<h2>{_e(title)}</h2>")
        parts.append(
            "<ul>" + "".join(f"<li>{_e(i)}</li>" for i in items) + "</ul>"
            if items
            else "<p>None.</p>"
        )
    parts += ["<script>", _JS, "</script></main></body></html>"]
    return "".join(parts) + "\n"
