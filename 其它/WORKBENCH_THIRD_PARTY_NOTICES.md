# ReviewDesk Reading 0.2 — third-party notices

ReviewDesk application source is MIT licensed. Components retain their own licenses; they are not all MIT.
Python is bundled with the Windows executable. The end user does not install Python, Node.js or Qt.
This v0.2 application does not bundle PySide6 or Qt; the separate legacy v0.1 application is unchanged.

- Python 3.14.6: PSF license and bundled third-party notices; see licenses/PYTHON-LICENSE.txt.
- PyInstaller: GPL with its bootloader exception; its full installed license is included.
- Inno Setup: installer compiler license in licenses/INNO-SETUP-LICENSE.txt.
- DeepSeek: optional external paid service, not a bundled model. No API key is shipped.

The Vue/Markdown production dependency closure is conservatively listed even when tree shaking removes some code.
The original license texts and machine-readable versions are in _internal/licenses/.

| Component | Version | License | Role |
| --- | --- | --- | --- |
| fastapi | 0.141.1 | MIT | runtime |
| starlette | 1.6.0 | BSD-3-Clause | runtime |
| pydantic | 2.13.5 | MIT | runtime |
| pydantic_core | 2.46.5 | MIT | runtime |
| annotated-types | 0.8.0 | MIT | runtime |
| annotated-doc | 0.0.5 | MIT | runtime |
| typing_extensions | 4.16.0 | PSF-2.0 | runtime |
| typing-inspection | 0.4.4 | MIT | runtime |
| anyio | 4.15.1 | MIT | runtime |
| idna | 3.20 | BSD-3-Clause | runtime |
| uvicorn | 0.53.0 | BSD-3-Clause | runtime |
| click | 8.5.0 | BSD-3-Clause | runtime |
| h11 | 0.16.0 | MIT | runtime |
| setuptools | 84.0.0 | MIT | runtime |
| PyInstaller | 6.22.2 | GPLv2-or-later with a special exception which allows to use PyInstaller to build and distribute non-free programs (including commercial ones) | build tool / bootloader |
| pyinstaller-hooks-contrib | 2026.7 | See included text | build tool / bootloader |
| altgraph | 0.17.5 | MIT | build tool / bootloader |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | build tool / bootloader |
| pywin32-ctypes | 0.2.3 | BSD-3-Clause | build tool / bootloader |
| pefile | 2024.8.26 | MIT | build tool / bootloader |
| @babel/helper-string-parser | 7.29.7 | MIT | production dependency closure; tree shaking may omit unused modules |
| @babel/helper-validator-identifier | 7.29.7 | MIT | production dependency closure; tree shaking may omit unused modules |
| @babel/parser | 7.29.9 | MIT | production dependency closure; tree shaking may omit unused modules |
| @babel/types | 7.29.8 | MIT | production dependency closure; tree shaking may omit unused modules |
| @jridgewell/sourcemap-codec | 1.6.0 | MIT | production dependency closure; tree shaking may omit unused modules |
| @vue/compiler-core | 3.5.43 | MIT | production dependency closure; tree shaking may omit unused modules |
| entities | 7.0.1 | BSD-2-Clause | production dependency closure; tree shaking may omit unused modules |
| @vue/compiler-dom | 3.5.43 | MIT | production dependency closure; tree shaking may omit unused modules |
| @vue/compiler-sfc | 3.5.43 | MIT | production dependency closure; tree shaking may omit unused modules |
| @vue/compiler-ssr | 3.5.43 | MIT | production dependency closure; tree shaking may omit unused modules |
| @vue/reactivity | 3.5.43 | MIT | production dependency closure; tree shaking may omit unused modules |
| @vue/runtime-core | 3.5.43 | MIT | production dependency closure; tree shaking may omit unused modules |
| @vue/runtime-dom | 3.5.43 | MIT | production dependency closure; tree shaking may omit unused modules |
| @vue/server-renderer | 3.5.43 | MIT | production dependency closure; tree shaking may omit unused modules |
| @vue/shared | 3.5.43 | MIT | production dependency closure; tree shaking may omit unused modules |
| argparse | 3.0.2 | PSF-2.0 | production dependency closure; tree shaking may omit unused modules |
| csstype | 3.2.3 | MIT | production dependency closure; tree shaking may omit unused modules |
| entities | 8.1.0 | BSD-2-Clause | production dependency closure; tree shaking may omit unused modules |
| estree-walker | 2.0.2 | MIT | production dependency closure; tree shaking may omit unused modules |
| linkify-it | 6.1.0 | MIT | production dependency closure; tree shaking may omit unused modules |
| magic-string | 0.30.21 | MIT | production dependency closure; tree shaking may omit unused modules |
| markdown-it | 15.0.2 | MIT | production dependency closure; tree shaking may omit unused modules |
| mdurl | 2.1.0 | MIT | production dependency closure; tree shaking may omit unused modules |
| nanoid | 3.3.19 | MIT | production dependency closure; tree shaking may omit unused modules |
| picocolors | 1.1.1 | ISC | production dependency closure; tree shaking may omit unused modules |
| postcss | 8.5.28 | MIT | production dependency closure; tree shaking may omit unused modules |
| punycode.js | 2.3.1 | MIT | production dependency closure; tree shaking may omit unused modules |
| source-map-js | 1.2.1 | BSD-3-Clause | production dependency closure; tree shaking may omit unused modules |
| uc.micro | 3.0.0 | MIT | production dependency closure; tree shaking may omit unused modules |
| vue | 3.5.43 | MIT | production dependency closure; tree shaking may omit unused modules |
