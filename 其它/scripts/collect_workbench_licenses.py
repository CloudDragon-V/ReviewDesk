"""Collect exact installed runtime licenses for the separately bundled Vue app."""

import importlib.metadata
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
OTHER = ROOT / "其它"
DEST = OTHER / "licenses-workbench"
WEB = ROOT / "前端功能" / "web"
PYTHON_RUNTIME = (
    "fastapi", "starlette", "pydantic", "pydantic_core", "annotated-types",
    "annotated-doc", "typing_extensions", "typing-inspection", "anyio", "idna",
    "uvicorn", "click", "h11",
    "setuptools",
)
BUILD_TOOLS = ("PyInstaller", "pyinstaller-hooks-contrib", "altgraph", "packaging",
               "pywin32-ctypes", "pefile")


def copy_license(source: Path, directory: Path, name: str | None = None) -> str:
    """Copy a license without flattening unrelated distributions together."""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (name or source.name)
    shutil.copy2(source, target)
    return target.relative_to(DEST).as_posix()


def main() -> None:
    """Produce a machine-readable inventory and human-readable distribution notice."""
    DEST.mkdir(parents=True, exist_ok=True)
    inventory: list[dict] = []
    for name in (*PYTHON_RUNTIME, *BUILD_TOOLS):
        distribution = importlib.metadata.distribution(name)
        metadata_directory = f"{distribution.metadata['Name'].replace('-', '_')}-{distribution.version}.dist-info"
        selected = [relative for relative in distribution.files or []
                    if ".dist-info/" in str(relative).replace("\\", "/")
                    and any(term in relative.name.lower() for term in ("license", "copying", "notice"))]
        if not selected:
            raise RuntimeError(f"Missing installed license text for {name} ({metadata_directory})")
        copied = [copy_license(Path(distribution.locate_file(relative)), DEST / "python-packages" / name,
                               f"{index:02d}-{relative.name}" if name == "setuptools" else None)
                  for index, relative in enumerate(selected) if Path(distribution.locate_file(relative)).is_file()]
        inventory.append({"ecosystem": "python", "name": name, "version": distribution.version,
                          "role": "runtime" if name in PYTHON_RUNTIME else "build tool / bootloader",
                          "license": distribution.metadata.get("License-Expression") or distribution.metadata.get("License", "See included text"),
                          "files": copied})
    lock = json.loads((WEB / "package-lock.json").read_text(encoding="utf-8"))
    for relative, metadata in sorted(lock["packages"].items()):
        if not relative or metadata.get("dev", False):
            continue
        directory = WEB / relative
        package = json.loads((directory / "package.json").read_text(encoding="utf-8"))
        selected = [file for file in directory.iterdir() if file.is_file()
                    and any(term in file.name.lower() for term in ("license", "copying", "notice"))]
        if not selected:
            raise RuntimeError(f"Missing installed frontend license: {relative}")
        package_name = package["name"]
        safe_name = package_name.replace("@", "").replace("/", "__") + "-" + metadata["version"]
        copied = [copy_license(file, DEST / "frontend-packages" / safe_name) for file in selected]
        inventory.append({"ecosystem": "npm", "name": package_name, "version": metadata["version"],
                          "role": "production dependency closure; tree shaking may omit unused modules",
                          "license": metadata.get("license", package.get("license", "See included text")), "files": copied})
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    inno_license = OTHER / ".tools" / "InnoSetup" / "license.txt"
    if not python_license.is_file() or not inno_license.is_file():
        raise RuntimeError("Python or Inno Setup license file missing")
    copy_license(python_license, DEST, "PYTHON-LICENSE.txt")
    copy_license(inno_license, DEST, "INNO-SETUP-LICENSE.txt")
    (DEST / "inventory.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows = ["# ReviewDesk Reading 0.2 — third-party notices", "",
            "ReviewDesk application source is MIT licensed. Components retain their own licenses; they are not all MIT.",
            "Python is bundled with the Windows executable. The end user does not install Python, Node.js or Qt.",
            "This v0.2 application does not bundle PySide6 or Qt; the separate legacy v0.1 application is unchanged.", "",
            "- Python " + sys.version.split()[0] + ": PSF license and bundled third-party notices; see licenses/PYTHON-LICENSE.txt.",
            "- PyInstaller: GPL with its bootloader exception; its full installed license is included.",
            "- Inno Setup: installer compiler license in licenses/INNO-SETUP-LICENSE.txt.",
            "- DeepSeek: optional external paid service, not a bundled model. No API key is shipped.", "",
            "The Vue/Markdown production dependency closure is conservatively listed even when tree shaking removes some code.",
            "The original license texts and machine-readable versions are in _internal/licenses/.", "",
            "| Component | Version | License | Role |", "| --- | --- | --- | --- |"]
    for item in inventory:
        license_name = str(item["license"]).replace("|", "/").replace("\n", " ")
        rows.append(f'| {item["name"]} | {item["version"]} | {license_name} | {item["role"]} |')
    (OTHER / "WORKBENCH_THIRD_PARTY_NOTICES.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(json.dumps({"components": len(inventory), "license_directory": str(DEST)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
