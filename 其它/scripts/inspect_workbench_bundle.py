"""Verify the final frozen bundle against current source without calling an API."""

from datetime import datetime, timezone
import hashlib
import json
import marshal
import types

from PyInstaller.archive.readers import CArchiveReader

from archive_workbench import ROOT, OTHER, credential_bytes, credential_hits, embedded_credential_scan


def normalize(code: types.CodeType) -> types.CodeType:
    """Ignore absolute compilation paths while comparing every nested code object."""
    return code.replace(co_filename="", co_consts=tuple(
        normalize(value) if isinstance(value, types.CodeType) else value for value in code.co_consts))


def main() -> None:
    """Inspect modules, licenses, bundled resources and exact credential absence."""
    bundle = OTHER / "运行文件" / "ReviewDeskReading"
    executable = bundle / "ReviewDeskReading.exe"
    archive = CArchiveReader(str(executable))
    pyz = archive.open_embedded_archive(next(name for name in archive.toc if name.endswith(".pyz")))
    checks = {}
    for module in sorted(name for name in pyz.toc if name == "reviewdesk" or name.startswith("reviewdesk.")):
        source = ROOT / "后端功能" / ("reviewdesk/__init__.py" if module == "reviewdesk" else module.replace(".", "/") + ".py")
        checks[module] = normalize(pyz.extract(module)) == normalize(compile(source.read_bytes(), str(source), "exec", dont_inherit=True))
    entry = ROOT / "前端功能" / "workbench.py"
    checks["entry:workbench.py"] = normalize(marshal.loads(archive.extract("workbench"))) == normalize(
        compile(entry.read_bytes(), str(entry), "exec", dont_inherit=True))
    files = [path for path in bundle.rglob("*") if path.is_file()]
    key = credential_bytes()
    inventory = json.loads((bundle / "_internal" / "licenses" / "inventory.json").read_text(encoding="utf-8"))
    result = {
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "source_modules_match_current_files": checks,
        "all_current_modules_match": all(checks.values()),
        "semantic_notes_bundled": "reviewdesk.semantic_notes" in pyz.toc,
        "manual_output_token_cap_16000": 16000 in pyz.extract("reviewdesk.manual_ai").co_consts,
        "bundled_python": (bundle / "_internal" / "python314.dll").is_file(),
        "qt_modules_absent": not any(name.startswith(("PySide6", "shiboken6", "reviewdesk_ui")) for name in pyz.toc),
        "foreign_icu_absent": not any(path.name.lower().startswith(("icuuc", "icudt", "icuin")) for path in files),
        "node_executable_absent": not any(path.name.lower() == "node.exe" for path in files),
        "credential_hits": sum(credential_hits(path.read_bytes(), key) for path in files),
        **embedded_credential_scan(executable, key),
        "license_inventory_count": len(inventory),
        "bundle_files": len(files), "bundle_bytes": sum(path.stat().st_size for path in files),
        "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(), "api_called": False,
    }
    required = ("all_current_modules_match", "semantic_notes_bundled", "manual_output_token_cap_16000",
                "bundled_python", "qt_modules_absent", "foreign_icu_absent", "node_executable_absent")
    if (not all(result[field] for field in required) or result["credential_hits"]
            or result["embedded_credential_hits"] or result["license_inventory_count"] != 50):
        raise RuntimeError("Frozen bundle inspection failed; do not release.")
    (OTHER / "verification" / "v02-bundle-inspection.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
