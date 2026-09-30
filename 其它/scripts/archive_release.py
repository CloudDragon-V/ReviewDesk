"""Create portable/source archives with explicit allowlists, then checksums."""
import argparse
from pathlib import Path
import hashlib
import json
import zipfile

ROOT = Path(__file__).resolve().parents[2]
OTHER = ROOT / "其它"
RELEASE = OTHER / "安装包"
RUNTIME = OTHER / "运行文件"
SOURCE_METADATA = (
    "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "pyproject.toml",
    "requirements.txt", "requirements-build.txt", ".gitignore", "AGENTS.md",
    "IMPLEMENTATION_CONTRACT.md", "思路代码.py", "目录说明.md",
)
# Each allowed directory has its own file types; never recurse through all of 其它.
SOURCE_FOLDERS = {
    "前端功能": {".py", ".pyi", ".md", ".txt", ".json", ".qss", ".svg", ".png", ".ico", ".cmd", ".bat"},
    "后端功能": {".py", ".pyi", ".md", ".txt", ".json"},
    "其它/scripts": {".py", ".ps1", ".cmd", ".bat", ".md"},
    "其它/tests": {".py", ".json", ".txt", ".md"},
    "其它/packaging": {".iss", ".md"},
    "其它/licenses": {".txt", ".md", ".html"},
    "其它/需求规则目录": {".md", ".txt", ".json"},
    "其它/evaluation": {".py", ".json", ".md", ".txt"},
    "其它/verification/迁移前工具快照": {".py", ".json", ".md", ".txt"},
}
EXCLUDED_DIRECTORIES = {
    ".venv", ".tools", ".idea", "__pycache__", ".pytest_cache", ".mypy_cache",
    "安装包", "运行文件", "构建缓存",
}
MAX_SOURCE_FILE_BYTES = 10 * 1024 * 1024


def source_paths(root: Path) -> list[Path]:
    """Select source and review evidence without local environments or binaries."""
    root = root.resolve()
    candidates = [root / "其它" / name for name in SOURCE_METADATA]
    for folder, suffixes in SOURCE_FOLDERS.items():
        candidates.extend(path for path in (root / folder).rglob("*")
                          if path.suffix.lower() in suffixes)
    # Verification subfolders may contain installed programs and user databases.
    # Only the explicitly allowed tool snapshot subtree above is recursive.
    candidates.extend(path for path in (root / "其它" / "verification").glob("*")
                      if path.suffix.lower() in {".md", ".json", ".txt", ".png"})
    return sorted({path for path in candidates
                   if path.is_file() and path.resolve().is_relative_to(root)
                   and not EXCLUDED_DIRECTORIES.intersection(path.relative_to(root).parts)
                   and path.stat().st_size <= MAX_SOURCE_FILE_BYTES})


def zip_paths(target: Path, paths: list[Path], base: Path) -> int:
    """Write selected files relative to their source or portable archive root."""
    count = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(set(paths)):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".db", ".log"}:
                archive.write(path, path.relative_to(base).as_posix())
                count += 1
    return count


def main() -> None:
    """Inspect the archive plan, or package the completed Windows build."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="List the source allowlist without writing archives.")
    args = parser.parse_args()
    source = source_paths(ROOT)
    if args.dry_run:
        print(json.dumps({"release_directory": str(RELEASE),
                          "portable_directory": str(RUNTIME / "ReviewDesk"),
                          "source_count": len(source),
                          "source_files": [path.relative_to(ROOT).as_posix() for path in source]},
                         ensure_ascii=False, indent=2))
        return
    if not (RUNTIME / "ReviewDesk" / "ReviewDesk.exe").is_file():
        raise SystemExit("Build 其它/运行文件/ReviewDesk/ReviewDesk.exe before creating archives.")
    RELEASE.mkdir(parents=True, exist_ok=True)
    portable_count = zip_paths(RELEASE / "ReviewDesk-0.1.0-Portable-win-x64.zip",
                               list((RUNTIME / "ReviewDesk").rglob("*")), RUNTIME)
    source_count = zip_paths(RELEASE / "ReviewDesk-0.1.0-Source.zip", source, ROOT)
    checksums = []
    for path in sorted(RELEASE.glob("*")):
        if path.suffix.lower() in {".exe", ".zip"}:
            checksums.append(hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name)
    (RELEASE / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="ascii")
    print(f"Portable files: {portable_count}; source files: {source_count}")
    print("\n".join(checksums))


if __name__ == "__main__":
    main()
