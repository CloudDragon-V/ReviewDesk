"""Collect upstream license texts from installed, version-pinned packages."""
import importlib.metadata
from pathlib import Path
import shutil
import sys

root = Path(__file__).resolve().parents[1]
dest = root / "licenses"
dest.mkdir(exist_ok=True)
for name in ("PySide6-Essentials", "shiboken6", "PyInstaller"):
    distribution = importlib.metadata.distribution(name)
    for relative in distribution.files or []:
        if "license" in str(relative).lower() or "copying" in str(relative).lower():
            source = Path(distribution.locate_file(relative))
            if source.is_file():
                target = dest / name / source.name
                target.parent.mkdir(exist_ok=True)
                shutil.copy2(source, target)
python_license = Path(sys.base_prefix) / "LICENSE.txt"
shutil.copy2(python_license, dest / "PYTHON-LICENSE.txt")
inno_license = root / ".tools" / "InnoSetup" / "license.txt"
if inno_license.exists():
    shutil.copy2(inno_license, dest / "INNO-SETUP-LICENSE.txt")
print("License texts collected:", len(list(dest.rglob("*"))))
