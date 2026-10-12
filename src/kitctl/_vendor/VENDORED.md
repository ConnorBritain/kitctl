# Vendored packages

| Package | Version | License | Source | sdist SHA-256 |
|---|---|---|---|---|
| tomlkit | 0.13.3 | MIT (`tomlkit/LICENSE`) | https://pypi.org/project/tomlkit/0.13.3/ | `430cf247ee57df2b94ee3fbe588e71d362a941ebb545dec29b53961d61add2a1` |

Why: kitctl edits Codex `config.toml` and must keep the user's comments, ordering, and
formatting. `tomllib` only reads; tomlkit round-trips. Vendoring keeps kitctl
dependency-free so the npm launcher's plain-Python fallback works.

Changes from upstream: the package's absolute imports (`from tomlkit...`) are rewritten to
`from kitctl._vendor.tomlkit...`. Nothing else is modified; `tests/test_vendor.py` checks
the file list and the import rewrite.

To update: download the sdist, verify its hash, copy `tomlkit/` and `LICENSE` here, rerun
`sed -i -E 's/^(\s*)from tomlkit(\.| import)/\1from kitctl._vendor.tomlkit\2/'` over the
`.py` files, and update this table.
