# Third-party components

ReviewDesk application source is MIT licensed. The following components retain their own copyrights and licenses. The distribution dynamically loads Qt/PySide libraries; replacement of those libraries and reverse engineering for debugging such modifications is permitted under their applicable licenses.

- Python 3.14.6 — Python Software Foundation license: https://docs.python.org/3/license.html
- PySide6-Essentials 6.11.2 / Shiboken6 6.11.2 / Qt 6.11.2 — LGPLv3 (and component-specific notices): https://doc.qt.io/qtforpython-6/licenses.html . Source: https://code.qt.io/cgit/pyside/pyside-setup.git/ and https://download.qt.io/archive/qt/6.11/6.11.2/single/ . Build/runtime source and relinking instructions: https://doc.qt.io/qtforpython-6/building_from_source/index.html . ReviewDesk build instructions are in README.md.
- PyInstaller 6.22.2 — GPLv2 with bootloader exception allowing distribution of bundled applications: https://pyinstaller.org/en/stable/license.html . Source: https://github.com/pyinstaller/pyinstaller . Build dependency only; its bootloader is included.
- Inno Setup 6.7.3 — installer compiler by Jordan Russell / Martijn Laan, https://jrsoftware.org/ . Source: https://github.com/jrsoftware/issrc/tree/is-6_7_3 . Used for this educational project; see its license for commercial compiler use.

Bundled license texts are in `_internal/licenses/`. Source archive excludes virtual environments, API credentials and user databases. Synthetic evaluation fixtures and revealed evaluation results are included for reproducibility.

DeepSeek is an external paid service, not a bundled model. ReviewDesk has no affiliation with DeepSeek or Qt. Pricing source (checked 2026-09-12): https://api-docs.deepseek.com/zh-cn/quick_start/pricing/ . Estimated cost is not a billing receipt.
