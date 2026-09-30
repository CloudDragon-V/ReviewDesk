"""Archive v0.2 with explicit source allowlists and no local databases or secrets."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import marshal
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
OTHER = ROOT / "其它"
RELEASE = OTHER / "安装包"
RUNTIME = OTHER / "运行文件"
PREFIX = "ReviewDeskReading-0.2.0"
EXCLUDED_DIRECTORIES = {
    "node_modules", ".venv", ".tools", ".idea", ".git", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".vite-temp", ".cache", "安装包", "运行文件", "构建缓存", "manuals", "userdata",
}
EXCLUDED_NAMES = {".env", "key.json", "keys.json", "credentials.json", "secrets.json", "settings.json",
                  "workbench-instance.json", "workbench.lock"}
# These release records include archive hashes and are published next to the ZIPs.
# Embedding them in Source.zip would make the manifest refer to an earlier archive.
RELEASE_ONLY_RECORDS = {"v02-packaging-status.json", "v02-archive-validation.json"}
SOURCE_METADATA = ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "WORKBENCH_THIRD_PARTY_NOTICES.md",
                   "pyproject.toml", "requirements.txt", "requirements-build.txt", ".gitignore",
                   "requirements-v02-lock.txt",
                   "IMPLEMENTATION_CONTRACT.md", "目录说明.md")
SOURCE_FOLDERS = {
    "前端功能": {".py", ".pyi", ".md", ".txt", ".json", ".vue", ".js", ".mjs", ".cjs", ".css",
                 ".html", ".qss", ".svg", ".png", ".ico", ".cmd", ".bat"},
    "后端功能": {".py", ".pyi", ".md", ".txt", ".json"},
    "其它/scripts": {".py", ".ps1", ".cmd", ".bat", ".md"},
    "其它/tests": {".py", ".json", ".txt", ".md"},
    "其它/packaging": {".iss", ".md"},
    "其它/licenses-workbench": None,
    "其它/需求规则目录": {".md", ".txt", ".json"},
    "其它/示例说明书": {".md", ".txt"},
    "其它/evaluation": {".py", ".json", ".md", ".txt"},
}


def allowed(path: Path, root: Path, suffixes: set[str] | None = None) -> bool:
    """Only package in-workspace plain files, never environments or user state."""
    relative = path.relative_to(root)
    return (path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root)
            and not EXCLUDED_DIRECTORIES.intersection(relative.parts)
            and path.name.casefold() not in EXCLUDED_NAMES and not path.name.casefold().startswith(".env.")
            and path.suffix.casefold() not in {".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".log", ".pyc", ".key", ".pem", ".spec"}
            and (suffixes is None or path.suffix.casefold() in suffixes))


def source_paths(root: Path = ROOT) -> list[Path]:
    """Walk a narrow allowlist, pruning node_modules before recursion."""
    root = root.resolve()
    candidates = {root / "其它" / name for name in SOURCE_METADATA}
    for folder, suffixes in SOURCE_FOLDERS.items():
        for directory, children, filenames in os.walk(root / folder, followlinks=False):
            children[:] = [name for name in children if name not in EXCLUDED_DIRECTORIES
                           and not (Path(directory) / name).is_symlink()]
            for filename in filenames:
                path = Path(directory) / filename
                if allowed(path, root, suffixes):
                    candidates.add(path)
    # Verification subfolders hold installed executables and app databases: never recurse.
    candidates.update(path for path in (root / "其它" / "verification").glob("*")
                      if path.name not in RELEASE_ONLY_RECORDS
                      and allowed(path, root, {".md", ".json", ".txt", ".png"}))
    return sorted(path for path in candidates if allowed(path, root) and path.stat().st_size <= 12 * 1024 * 1024)


def write_archive(target: Path, paths: list[Path], base: Path) -> int:
    """Write selected files without following filesystem links."""
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in paths:
            archive.write(path, path.relative_to(base).as_posix())
    return len(paths)


def credential_bytes() -> bytes:
    """Read the current OS credential into memory only; never print or persist it."""
    sys.path.insert(0, str(ROOT / "后端功能"))
    from reviewdesk.storage import CredentialStore
    key = CredentialStore().get_key()
    if not key:
        raise RuntimeError("无法完成精确凭据扫描：Windows 凭据管理器中没有当前应用的密钥。")
    return key.encode("utf-8")


def credential_hits(data: bytes, key: bytes) -> int:
    """Recognize plain UTF-8/UTF-16 and escaped JSON without reporting any value."""
    text = key.decode("utf-8")
    alternatives = (key, text.encode("utf-16-le"), text.encode("utf-16-be"),
                    "".join(f"\\u{ord(character):04x}" for character in text).encode("ascii"))
    return int(any(value in data for value in alternatives))


def embedded_credential_scan(executable: Path, key: bytes) -> dict:
    """Inspect decompressed application bytecode as well as raw executable bytes."""
    from PyInstaller.archive.readers import CArchiveReader
    archive = CArchiveReader(str(executable))
    pyz = archive.open_embedded_archive(next(name for name in archive.toc if name.endswith(".pyz")))
    modules = [name for name in pyz.toc if name == "reviewdesk" or name.startswith("reviewdesk.")]
    hits = sum(credential_hits(marshal.dumps(pyz.extract(name)), key) for name in modules)
    if "workbench" in archive.toc:
        hits += credential_hits(archive.extract("workbench"), key)
    return {"embedded_application_modules_checked": len(modules) + 1, "embedded_credential_hits": hits}


def preflight_credential_scan(source: list[Path], portable: list[Path], executable: Path) -> dict:
    """Stop before packaging if the supplied user credential appears in any artifact."""
    key = credential_bytes()
    paths = sorted(set(source + portable))
    raw_hits = sum(credential_hits(path.read_bytes(), key) for path in paths)
    embedded = embedded_credential_scan(executable, key)
    result = {"credential_available": True, "files_checked": len(paths), "credential_hits": raw_hits,
              **embedded, "api_called": False}
    if raw_hits or embedded["embedded_credential_hits"]:
        raise RuntimeError(f"凭据扫描拒绝打包；文件命中数={raw_hits}，内嵌模块命中数={embedded['embedded_credential_hits']}。")
    return result


def main() -> None:
    """Always package portable files; include source only when explicitly requested."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="store_true", help="Also create source archive after documentation/evaluation freeze.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--check-secrets", action="store_true", help="Only scan source/bundle and embedded modules; create no archives.")
    arguments = parser.parse_args()
    source = source_paths()
    if arguments.dry_run:
        print(json.dumps({"source_count": len(source), "files": [path.relative_to(ROOT).as_posix() for path in source]},
                         ensure_ascii=False, indent=2))
        return
    bundle = RUNTIME / "ReviewDeskReading"
    if not (bundle / "ReviewDeskReading.exe").is_file():
        raise SystemExit("Build ReviewDeskReading.exe before creating a portable archive.")
    RELEASE.mkdir(parents=True, exist_ok=True)
    portable = sorted(path for path in bundle.rglob("*") if allowed(path, RUNTIME))
    scan = preflight_credential_scan(source, portable, bundle / "ReviewDeskReading.exe")
    (OTHER / "verification" / "v02-prearchive-secret-scan.json").write_text(
        json.dumps(scan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if arguments.check_secrets:
        print(json.dumps(scan, ensure_ascii=False, indent=2))
        return
    previous = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in RELEASE.glob("*") if path.is_file() and path.suffix in {".exe", ".zip"}
                and not path.name.startswith(PREFIX)}
    portable_count = write_archive(RELEASE / f"{PREFIX}-Portable-win-x64.zip", portable, RUNTIME)
    source_count = write_archive(RELEASE / f"{PREFIX}-Source.zip", source, ROOT) if arguments.source else None
    rows = [hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name
            for path in sorted(RELEASE.glob(PREFIX + "*")) if path.suffix in {".exe", ".zip"}]
    (RELEASE / f"{PREFIX}-SHA256SUMS.txt").write_text("\n".join(rows) + "\n", encoding="ascii")
    all_rows = [hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name
                for path in sorted(RELEASE.glob("*")) if path.is_file() and path.suffix in {".exe", ".zip"}]
    (RELEASE / "SHA256SUMS.txt").write_text("\n".join(all_rows) + "\n", encoding="ascii")
    if any(hashlib.sha256((RELEASE / name).read_bytes()).hexdigest() != digest for name, digest in previous.items()):
        raise RuntimeError("An existing legacy artifact changed during packaging; release verification stopped.")
    print(json.dumps({"portable_files": portable_count, "source_files": source_count, "checksums": rows,
                      "all_release_checksums": len(all_rows), "legacy_artifacts_preserved": len(previous),
                      "credential_scan": scan}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
