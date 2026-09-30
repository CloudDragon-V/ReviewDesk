"""Check a PyInstaller bundle for key absence and the current merge fix."""
import argparse
import json
from pathlib import Path
import sys
import types

from PyInstaller.archive.readers import CArchiveReader

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, default=root / "verification" / "bundle-inspection.json")
args = parser.parse_args()
sys.path.insert(0, str(root.parent / "后端功能"))
from reviewdesk.storage import CredentialStore

bundle = root / "运行文件" / "ReviewDesk"
archive = CArchiveReader(str(bundle / "ReviewDesk.exe"))
pyz_name = next(name for name in archive.toc if name.endswith(".pyz"))
pyz = archive.open_embedded_archive(pyz_name)
service = pyz.extract("reviewdesk.service")

def constants(code):
    for value in code.co_consts:
        if isinstance(value, types.CodeType):
            yield from constants(value)
        elif isinstance(value, str):
            yield value

strings = list(constants(service))
patched = any("可变.*默认|默认.*可变|mutable.*default|default.*mutable" in value for value in strings)
key = CredentialStore().get_key().encode("utf-8")
leaks = []
scanned = 0
for path in bundle.rglob("*"):
    if path.is_file():
        scanned += 1
        if key and key in path.read_bytes():
            leaks.append(path.relative_to(bundle).as_posix())
result = {"final_merge_fix_in_pyz": patched, "files_scanned": scanned, "credential_matches": leaks,
          "runtime_bundled": (bundle / "_internal" / "python314.dll").exists(),
          "qt_plugin_bundled": (bundle / "_internal" / "PySide6" / "plugins" / "platforms" / "qwindows.dll").exists()}
result["foreign_icu_absent"] = not (bundle / "_internal" / "icuuc.dll").exists()
result["frontend_package_bundled"] = "reviewdesk_ui.ui" in pyz.toc and "reviewdesk_ui.widgets" in pyz.toc
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result))
if not patched or leaks or not result["runtime_bundled"] or not result["qt_plugin_bundled"] or not result["foreign_icu_absent"] or not result["frontend_package_bundled"]:
    raise SystemExit(1)
