"""Map the single PEP 440 version (``kitctl.__version__``) to npm semver and dist-tags."""

from __future__ import annotations

import re

_PEP440 = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+)|\.dev(\d+))?$")
_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-(?:(alpha|beta|rc)\.(\d+)|0\.dev\.(\d+)))?$")
_TO_SEMVER = {"a": "alpha", "b": "beta", "rc": "rc"}
_TO_PEP440 = {value: key for key, value in _TO_SEMVER.items()}


def pep440_to_semver(version: str) -> str:
    """``0.1.0rc1`` -> ``0.1.0-rc.1``; ``0.1.0.dev2`` -> ``0.1.0-0.dev.2`` (sorts before alpha)."""
    match = _PEP440.match(version)
    if not match:
        raise ValueError(f"Unsupported version for npm mapping: {version!r}")
    major, minor, patch, label, number, dev = match.groups()
    core = f"{major}.{minor}.{patch}"
    if label:
        return f"{core}-{_TO_SEMVER[label]}.{number}"
    if dev is not None:
        return f"{core}-0.dev.{dev}"
    return core


def semver_to_pep440(version: str) -> str:
    match = _SEMVER.match(version)
    if not match:
        raise ValueError(f"Unsupported semver for PEP 440 mapping: {version!r}")
    major, minor, patch, label, number, dev = match.groups()
    core = f"{major}.{minor}.{patch}"
    if label:
        return f"{core}{_TO_PEP440[label]}{number}"
    if dev is not None:
        return f"{core}.dev{dev}"
    return core


def is_prerelease(version: str) -> bool:
    return "-" in pep440_to_semver(version)


def npm_dist_tag(version: str) -> str:
    """Pre-releases publish under ``next`` so ``npx kitctl`` keeps resolving to stable."""
    return "next" if is_prerelease(version) else "latest"
