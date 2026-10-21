# Publishing

## 0.1.0 — prepared 2026-10-21, not yet published

The owner publishes; nothing is uploaded from a cloud session. Same procedure as 0.0.1
below, with the new tag:

1. Merge the release PR to main and confirm CI is green on Linux and Windows.
2. Tag the merge commit: `git tag v0.1.0 <sha> && git push origin v0.1.0`.
3. `gh workflow run publish-python.yml --ref v0.1.0 -f target=testpypi`, then
   `uvx --index https://test.pypi.org/simple/ --from kitctl==0.1.0 kitctl --version`.
4. `gh workflow run publish-python.yml --ref v0.1.0 -f target=pypi`, then
   `uvx --from kitctl==0.1.0 kitctl --version`.
5. npm, from the same tag: the [npm](#npm) steps with `kitctl-0.1.0.tgz`.
6. Smoke test in a temporary HOME: `kitctl init`, `kitctl validate`,
   `kitctl use starter --repo <tmp repo> --yes`, `kitctl off starter --repo <tmp repo> --yes`.

# Publishing 0.0.1

## Verified release record — 2026-10-11

- [npm kitctl 0.0.1](https://www.npmjs.com/package/kitctl/v/0.0.1) is published
  under connorbritain; registry integrity matches the locally tested tarball.
- [TestPyPI kitctl 0.0.1](https://test.pypi.org/project/kitctl/0.0.1/) was published
  by [the rehearsal workflow](https://github.com/ConnorBritain/kitctl/actions/runs/38142749776).
- [PyPI kitctl 0.0.1](https://pypi.org/project/kitctl/0.0.1/) was published
  by [the production workflow](https://github.com/ConnorBritain/kitctl/actions/runs/38142825980).
- Both Python releases use tag v0.0.1, commit e1a0dde219043badf11d9208ee0bde895329a076.
  Wheel and source archive SHA-256 hashes match exactly across the two indexes.
- Fresh installs from all three registries passed --version and read-only inventory
  against temporary repositories, with isolated HOME and agent config directories.
- GitHub trusted publishers are active on both Python indexes; no local upload token
  was created. Namespace reservation on npm and PyPI is complete.

The remaining sections document the setup and release procedure. Do not rerun
an upload of an already published version.

The user's request authorizes immediate package name reservation through a useful
first release. Do not call a namespace held until registry metadata verifies the
version and owner. Empty packages are prohibited by
[PyPI name-retention policy](https://docs.pypi.org/project-management/name-retention/).

## Account setup

PyPI needs an account, verified email, and 2FA. TestPyPI is a separate service with
its own account. Sign in or register:

- https://pypi.org/account/register/
- https://test.pypi.org/account/register/

Prefer [pending trusted publishers](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
for the first release, so no long-lived token has to be shared. On **each** registry,
open Publishing, add a pending GitHub publisher with:

| Field | Value |
|---|---|
| PyPI project name | kitctl |
| Owner | ConnorBritain |
| Repository | kitctl |
| Workflow filename | publish-python.yml |
| Environment | testpypi on TestPyPI; pypi on PyPI |

A pending publisher alone does not reserve the project name. A successful first
upload creates it. GitHub environments with those names should exist. The workflow
supports explicit dispatch only and uses OIDC in the publishing job. Run it from
the reviewed release tag, target TestPyPI first, install/test that result, then
dispatch the same tag for PyPI. The workflow does not automatically enforce the
cross-registry ordering; the release operator must verify TestPyPI success.

```sh
gh workflow run publish-python.yml --ref v0.0.1 -f target=testpypi
# After successful TestPyPI publication:
uvx --index https://test.pypi.org/simple/ --from kitctl==0.0.1 kitctl --version
# Then publish the same reviewed tag to production:
gh workflow run publish-python.yml --ref v0.0.1 -f target=pypi
uvx --from kitctl==0.0.1 kitctl --version
```

## npm

Build and verify the exact tarball:

```sh
uv sync --locked
uv run pytest
uv build
uv run python scripts/prepare_npm.py
npm --prefix npm test
cd npm
npm pack --dry-run
npm pack
npm publish kitctl-0.0.1.tgz --access public
npm view kitctl@0.0.1 version maintainers dist.integrity
```

Publishing requires [2FA or an eligible granular token](https://docs.npmjs.com/requiring-2fa-for-package-publishing-and-settings-modification/).
An authenticated `npm whoami` does not prove publish authorization. Let the account
owner complete interactive authentication if required; don't copy tokens into the
repository. The package includes only the launcher, wheel, README, and MIT license.
No real inventory, warehouse content, or agent config belongs in either package.

Both ecosystems publish public artifacts even if the source repository is private.
For private source, use manual Python uploads or validate current registry support
for that repository's trusted publishing before dispatch.
