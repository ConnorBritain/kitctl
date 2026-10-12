"""Bundle the exact Python wheel so npm doesn't depend on a prior PyPI release.

Also writes npm/package.json's version (semver form of kitctl.__version__).
"""

import json
import shutil
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "scripts"))

from version import pep440_to_semver, read_version  # noqa: E402

version = read_version()
wheels = sorted((root / "dist").glob(f"kitctl-{version}-py3-none-any.whl"))
if len(wheels) != 1:
    raise SystemExit(f"Expected one kitctl {version} wheel in dist/; run uv build first.")
wheel = wheels[0]
target = root / "npm" / "python"
target.mkdir(exist_ok=True)
for old in target.glob("*.whl"):
    old.unlink()
shutil.copy2(wheel, target / wheel.name)
for name in ("README.md", "LICENSE"):
    shutil.copy2(root / name, root / "npm" / name)
manifest = root / "npm" / "package.json"
package = json.loads(manifest.read_text("utf-8"))
package["version"] = pep440_to_semver(version)
manifest.write_text(json.dumps(package, indent=2) + "\n", "utf-8")
print(f"Bundled {wheel.name}; npm version {package['version']}")
