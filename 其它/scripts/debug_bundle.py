"""Use the matching console bootloader with the existing package for diagnosis."""
from pathlib import Path
import PyInstaller
import subprocess
import shutil

root = Path(__file__).resolve().parents[1]
bundle = root / "运行文件" / "ReviewDesk"
target = bundle / "ReviewDesk-Diagnostic.exe"
loader = Path(PyInstaller.__file__).parent / "bootloader" / "Windows-64bit-intel" / "run.exe"
package = root / "构建缓存" / "ReviewDesk" / "ReviewDesk.pkg"
with target.open("wb") as out:
    out.write(loader.read_bytes())
    out.write(package.read_bytes())
try:
    result = subprocess.run([str(target), "--smoke-test", str(root / "verification" / "diagnostic-smoke.json")],
                            capture_output=True, timeout=45)
    text = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    (root / "verification" / "bundle-diagnostic.txt").write_text(text, encoding="utf-8")
    print(text)
    print("Diagnostic exit:", result.returncode)
finally:
    target.unlink(missing_ok=True)
