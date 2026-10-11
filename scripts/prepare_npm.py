"""Bundle the exact Python wheel so npm doesn't depend on a prior PyPI release."""

import shutil
import tomllib
from pathlib import Path

root = Path(__file__).resolve().parents[1]
version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
wheel = root / "dist" / f"kitctl-{version}-py3-none-any.whl"
if not wheel.is_file():
    raise SystemExit("Run uv build first.")
target = root / "npm" / "python"
target.mkdir(exist_ok=True)
for old in target.glob("*.whl"):
    old.unlink()
shutil.copy2(wheel, target / wheel.name)
for name in ("README.md", "LICENSE"):
    shutil.copy2(root / name, root / "npm" / name)
print(f"Bundled {wheel.name}")
