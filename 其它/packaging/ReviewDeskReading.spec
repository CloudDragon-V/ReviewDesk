# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['D:/基于大模型的代码审查辅助工具设计与实现/前端功能/workbench.py'],
    pathex=['D:/基于大模型的代码审查辅助工具设计与实现/后端功能'],
    binaries=[],
    datas=[('D:/基于大模型的代码审查辅助工具设计与实现/前端功能/web/dist', 'web'), ('D:/基于大模型的代码审查辅助工具设计与实现/其它/licenses-workbench', 'licenses'), ('D:/基于大模型的代码审查辅助工具设计与实现/其它/LICENSE', '.'), ('D:/基于大模型的代码审查辅助工具设计与实现/其它/WORKBENCH_THIRD_PARTY_NOTICES.md', '.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PySide6', 'shiboken6', 'tkinter', 'pytest', 'httpx', 'httpcore', 'uvicorn.loops.uvloop', 'uvicorn.protocols.http.httptools_impl', 'uvicorn.protocols.websockets'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ReviewDeskReading',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['D:/基于大模型的代码审查辅助工具设计与实现/前端功能/resources/reviewdesk.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ReviewDeskReading',
)
