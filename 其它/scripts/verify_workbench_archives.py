"""Validate final ZIPs, scan credentials, then run the safely extracted portable app."""

import hashlib
import json
import marshal
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import types
import uuid
import zipfile
from datetime import datetime, timezone

from archive_workbench import (
    ROOT, OTHER, RELEASE, PREFIX, EXCLUDED_DIRECTORIES, EXCLUDED_NAMES,
    credential_bytes, credential_hits, embedded_credential_scan,
)

FORBIDDEN_SUFFIXES = {".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".log", ".key", ".pem", ".spec", ".pyc"}
MAX_ZIP_BYTES = 512 * 1024 * 1024


def inspect_zip(path: Path, kind: str, key: bytes) -> dict:
    """Verify checksums and entry boundaries before any extraction or execution."""
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(entries) > 10000 or sum(entry.file_size for entry in entries) > MAX_ZIP_BYTES:
            raise RuntimeError("Archive size or entry count exceeds verification limits.")
        if len(set(name.casefold() for name in names)) != len(names):
            raise RuntimeError("Archive has duplicate paths under Windows case comparison.")
        for entry in entries:
            name = entry.filename
            path_parts = PurePosixPath(name)
            if (not name or "\\" in name or ":" in name or path_parts.is_absolute()
                    or ".." in path_parts.parts or str(path_parts) != name
                    or any(ord(character) < 32 for character in name)
                    or (entry.external_attr >> 16) & 0o170000 == 0o120000):
                raise RuntimeError("Archive contains an unsafe path or symbolic link.")
            parts = set(path_parts.parts)
            if EXCLUDED_DIRECTORIES.intersection(parts) or path_parts.name.casefold() in EXCLUDED_NAMES:
                raise RuntimeError("Archive contains a forbidden environment/user-data path.")
            if path_parts.suffix.casefold() in FORBIDDEN_SUFFIXES or path_parts.name.casefold().startswith(".env."):
                raise RuntimeError("Archive contains a database, log, key or generated spec file.")
            if entry.file_size > 64 * 1024 * 1024:
                raise RuntimeError("Archive member exceeds verification limits.")
        if archive.testzip() is not None:
            raise RuntimeError("Archive CRC check failed.")
        secret_hits = sum(credential_hits(archive.read(entry), key) for entry in entries)
        if secret_hits:
            raise RuntimeError(f"凭据扫描拒绝归档：命中数={secret_hits}。")
        if kind == "source":
            required = {
                "前端功能/workbench.py", "前端功能/启动软件.cmd", "前端功能/启动经典版.cmd",
                "前端功能/web/package.json", "前端功能/web/package-lock.json", "前端功能/web/src/App.vue",
                "前端功能/web/dist/index.html", "后端功能/reviewdesk/web_api.py", "后端功能/reviewdesk/knowledge.py",
                "后端功能/reviewdesk/manual.py", "其它/README.md", "其它/scripts/build_workbench.ps1",
                "其它/requirements-v02-lock.txt", "其它/verification/v0.2验收报告.md",
                "其它/packaging/reviewdesk-workbench.iss", "其它/licenses-workbench/inventory.json",
            }
            if {PurePosixPath(name).parts[0] for name in names} != {"前端功能", "后端功能", "其它"}:
                raise RuntimeError("Source archive must have exactly the three project folders.")
            if not any(name.startswith("其它/示例说明书/") and name.endswith(".md") for name in names):
                raise RuntimeError("Source archive is missing the example manual.")
            asset_prefix = "前端功能/web/dist/assets/"
        else:
            required = {"ReviewDeskReading/ReviewDeskReading.exe", "ReviewDeskReading/README.md",
                        "ReviewDeskReading/_internal/python314.dll", "ReviewDeskReading/_internal/web/index.html",
                        "ReviewDeskReading/_internal/licenses/inventory.json"}
            asset_prefix = "ReviewDeskReading/_internal/web/assets/"
        if not required.issubset(names):
            raise RuntimeError("Archive is missing a required application/source component.")
        if not all(any(name.startswith(asset_prefix) and name.endswith(suffix) for name in names) for suffix in (".js", ".css")):
            raise RuntimeError("Archive lacks production JavaScript or CSS resources.")
        return {"kind": kind, "file_count": len(entries), "uncompressed_bytes": sum(entry.file_size for entry in entries),
                "crc_ok": True, "required_paths_present": True, "forbidden_paths_absent": True,
                "credential_hits": secret_hits, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def verify_checksums() -> int:
    """Check both release-wide and v0.2-only manifests without changing old files."""
    total = 0
    for manifest in (RELEASE / "SHA256SUMS.txt", RELEASE / f"{PREFIX}-SHA256SUMS.txt"):
        rows = manifest.read_text(encoding="ascii").splitlines()
        seen = set()
        for row in rows:
            digest, name = row.split("  ", 1)
            if Path(name).name != name or name in seen:
                raise RuntimeError("Invalid checksum manifest entry.")
            seen.add(name)
            if hashlib.sha256((RELEASE / name).read_bytes()).hexdigest() != digest:
                raise RuntimeError("Release checksum mismatch.")
            total += 1
        expected = {path.name for path in RELEASE.glob("*") if path.suffix in {".exe", ".zip"}
                    and (manifest.name == "SHA256SUMS.txt" or path.name.startswith(PREFIX))}
        if seen != expected:
            raise RuntimeError("Checksum manifest does not cover all expected artifacts.")
    return total


def compare_packaged_sources(executable: Path, source_archive: Path) -> dict:
    """Prove frozen Python bytecode and Vue assets belong to the delivered source ZIP."""
    from PyInstaller.archive.readers import CArchiveReader

    def normalize(code: types.CodeType) -> types.CodeType:
        return code.replace(co_filename="", co_consts=tuple(
            normalize(value) if isinstance(value, types.CodeType) else value for value in code.co_consts))

    packaged = CArchiveReader(str(executable))
    pyz = packaged.open_embedded_archive(next(name for name in packaged.toc if name.endswith(".pyz")))
    modules = [name for name in pyz.toc if name == "reviewdesk" or name.startswith("reviewdesk.")]
    with zipfile.ZipFile(source_archive) as source:
        for module in modules:
            path = "后端功能/" + ("reviewdesk/__init__.py" if module == "reviewdesk" else module.replace(".", "/") + ".py")
            source_code = compile(source.read(path), path, "exec", dont_inherit=True)
            if normalize(source_code) != normalize(pyz.extract(module)):
                raise RuntimeError("Frozen backend code differs from the source archive; rebuild before release.")
        entry = compile(source.read("前端功能/workbench.py"), "workbench.py", "exec", dont_inherit=True)
        if normalize(entry) != normalize(marshal.loads(packaged.extract("workbench"))):
            raise RuntimeError("Frozen entry point differs from the source archive.")
        assets = [name for name in source.namelist() if name.startswith("前端功能/web/dist/")]
        for name in assets:
            path = executable.parent / "_internal" / "web" / name.removeprefix("前端功能/web/dist/")
            if not path.is_file() or path.read_bytes() != source.read(name):
                raise RuntimeError("Portable Vue resources differ from the source archive.")
    return {"python_modules_verified": len(modules) + 1, "python_bytecode_matches_source": True,
            "frontend_dist_files_verified": len(assets), "frontend_dist_matches_source_archive": True}


def main() -> None:
    """Run final validation only after installer, portable and source archives freeze."""
    evidence = (OTHER / "verification").resolve()
    key = credential_bytes()
    portable = RELEASE / f"{PREFIX}-Portable-win-x64.zip"
    source = RELEASE / f"{PREFIX}-Source.zip"
    report = {"api_called": False, "credential_available": True,
              "verified_at": datetime.now(timezone.utc).isoformat(),
              "portable": inspect_zip(portable, "portable", key),
              "source": inspect_zip(source, "source", key), "checksum_entries_verified": verify_checksums()}
    destination = (evidence / ("v02-extracted-" + uuid.uuid4().hex[:10])).resolve()
    if not destination.is_relative_to(evidence) or destination.exists():
        raise RuntimeError("Unsafe or existing extraction directory.")
    destination.mkdir()
    with zipfile.ZipFile(portable) as archive:
        for entry in archive.infolist():
            target = (destination / entry.filename).resolve()
            if not target.is_relative_to(destination):
                raise RuntimeError("Extraction path left the verification directory.")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(entry))
    executable = destination / "ReviewDeskReading" / "ReviewDeskReading.exe"
    report["embedded"] = embedded_credential_scan(executable, key)
    if report["embedded"]["embedded_credential_hits"]:
        raise RuntimeError("内嵌模块凭据扫描未通过；不运行解压程序。")
    report["source_consistency"] = compare_packaged_sources(executable, source)
    env = dict(os.environ)
    # os.environ is case-insensitive on Windows; a copied dict is not.
    system_root = os.environ["SystemRoot"]
    env["PATH"] = os.path.join(system_root, "System32") + os.pathsep + system_root
    env["PYTHONPATH"] = "C:\\ReviewDesk-No-Python"
    env.pop("PYTHONHOME", None)
    smoke = destination / "portable-smoke.json"
    data = destination / "user-data"
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    completed = subprocess.run([str(executable), "--no-browser", "--data-dir", str(data), "--smoke-test", str(smoke)],
                               cwd=destination, env=env, startupinfo=startup,
                               creationflags=subprocess.CREATE_NO_WINDOW, timeout=180, check=False)
    if completed.returncode or not smoke.is_file():
        raise RuntimeError("Extracted portable application failed to run.")
    result = json.loads(smoke.read_text(encoding="utf-8"))
    if not all(result.get(field) for field in ("ok", "frozen", "auto_exported", "download_verified", "frontend_served")) or result.get("api_called"):
        raise RuntimeError("Extracted portable smoke assertions failed.")
    report.update(extracted_run=result, extracted_path=str(destination), system_only_path=env["PATH"],
                  process_exit=completed.returncode, other_physical_computer_tested=False, ok=True)
    (evidence / "v02-archive-validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
