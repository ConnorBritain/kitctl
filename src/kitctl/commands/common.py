"""Helpers shared by command handlers: exit codes and the output guards."""

from __future__ import annotations

import argparse
from typing import Any

EXIT_OK, EXIT_FINDINGS, EXIT_USAGE = 0, 1, 2


def guard(report: dict[str, Any]) -> dict[str, Any]:
    """Final guard: reports carry paths and metadata only, but anything secret-shaped goes."""
    from kitctl.secretscan import redact_obj

    cleaned, kinds = redact_obj(report)
    if kinds:
        cleaned["findings"].append(
            {
                "severity": "warning",
                "code": "secret-redacted",
                "path": "",
                "message": f"Redacted likely secrets from output ({', '.join(kinds)})",
            }
        )
    result: dict[str, Any] = cleaned
    return result


def guard_any(value: dict[str, Any]) -> dict[str, Any]:
    """Redact secret-shaped values from any output, warning on stderr."""
    import sys

    from kitctl.secretscan import redact_obj

    cleaned, kinds = redact_obj(value)
    if kinds:
        print(f"warning: redacted likely secrets from output ({', '.join(kinds)})", file=sys.stderr)
    result: dict[str, Any] = cleaned
    return result


def descriptions(report: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    """Drop free-text descriptions when asked (--no-descriptions or config)."""
    from kitctl import config as kit_config
    from kitctl import paths as kit_paths

    keep = not getattr(args, "no_descriptions", False)
    if keep:
        try:
            settings = kit_config.load(kit_paths.resolve().kit_config_file)
        except kit_config.ConfigError:
            settings = None
        keep = settings.export_descriptions if settings else True
    if not keep:
        for entry in report.get("entries", []):
            entry["metadata"].pop("description", None)
    return report


def exit_for(report: dict[str, Any]) -> int:
    errors = any(finding["severity"] == "error" for finding in report["findings"])
    return EXIT_FINDINGS if errors else EXIT_OK
