# Publishing kitctl

## Release flow (0.1.0 onward)

Releases are tag-driven through `.github/workflows/release.yml`. The version lives only in
`src/kitctl/__init__.py` (PEP 440, e.g. `0.1.0rc1`); npm gets the semver form
(`0.1.0-rc.1`) via `scripts/prepare_npm.py`. Pre-releases go to npm under the `next`
dist-tag, so `npx kitctl` keeps resolving to the latest stable; stable releases go to `latest`.

1. On the release branch: set `__version__`, run `uv run python scripts/prepare_npm.py`
   (after `uv build`) to update `npm/package.json`, and rename the CHANGELOG's
   "Unreleased" heading to `## <version> — <date>`. `scripts/version.py --check` must pass.
2. Push the tag: `git tag v0.1.0rc1 <commit> && git push origin v0.1.0rc1`.
3. The workflow then:
   - checks that tag, version, npm version, and CHANGELOG section agree;
   - runs the full CI matrix (Linux, Windows, macOS × Python 3.11, 3.14);
   - builds the wheel, sdist, and npm tarball once (with SHA256SUMS);
   - installs those artifacts on all three OSes via `uv tool install`, `pipx install`,
     and the npm tarball (with uv, and with the plain-Python fallback), running
     `kitctl --version` and `kitctl doctor` in a sandbox HOME;
   - publishes to **TestPyPI** (environment `testpypi`), installs from TestPyPI on all
     three OSes, then publishes to **PyPI** (environment `pypi`) and **npm** (environment
     `npm`), using the same artifacts;
   - installs from the public registries on all three OSes and smoke-tests;
   - creates the GitHub release with notes from the CHANGELOG section.

**Dry run.** Pushing a branch commit that touches release mechanics (the workflow,
version/npm/release scripts, `npm/`, `pyproject.toml`, `src/kitctl/__init__.py`), or a
manual "Run workflow" dispatch once the workflow is on the default branch, runs verify,
CI, build, and install-smoke on all three OSes and skips every publish job.

## One-time setup (account owner)

- **Required reviewers.** In GitHub → Settings → Environments, add yourself as a required
  reviewer on `testpypi`, `pypi`, and `npm`. Every publish then waits for your approval in
  the Actions run. Without reviewers, a pushed tag publishes automatically.
- **PyPI/TestPyPI.** The existing trusted publishers name `publish-python.yml`. Add a
  second trusted publisher on each index for workflow `release.yml` with the matching
  environment (`testpypi` / `pypi`). Keep the old one for manual fallback.
- **npm.** Preferred: on npmjs.com → kitctl → Settings → Trusted publishing, add GitHub
  Actions publisher `ConnorBritain/kitctl`, workflow `release.yml`, environment `npm`
  (requires npm CLI 11.5.1+, which the workflow installs). Fallback: create a granular
  automation token limited to `kitctl` and store it as the `NPM_TOKEN` secret on the `npm`
  environment. If neither is configured, the npm job fails and nothing else is affected;
  publish manually:

  ```sh
  # from the release's artifacts (or a local build of the same tag)
  npm publish ./kitctl-0.1.0-rc.1.tgz --access public --tag next
  npm view kitctl dist-tags
  ```

- **Deprecate the leftover placeholder** (once rc1 is on npm):

  ```sh
  npm deprecate kitctl@0.0.0-stage "Placeholder; install kitctl@latest or kitctl@next"
  ```

## Manual fallback

`publish-python.yml` (workflow_dispatch) can still publish a reviewed tag to TestPyPI or
PyPI by hand: `gh workflow run publish-python.yml --ref v0.1.0rc1 -f target=testpypi`.

## Verified history

- 0.0.1 (2026-10-11): npm, TestPyPI, and PyPI published and installation-tested; tag
  v0.0.1, commit e1a0dde. Namespaces are held on npm and PyPI.
