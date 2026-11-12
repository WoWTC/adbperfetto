# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: onefile adbperfetto.exe.

Bundles adbperfetto/web and adbperfetto/presets under adbperfetto/
inside the package so paths.resource_dir() finds them in _MEIPASS.
adb is NOT bundled — it is resolved from PATH at runtime.
"""
from PyInstaller.utils.hooks import collect_submodules

a = Analysis(
    ["run_adbperfetto.py"],
    pathex=["."],
    binaries=[],
    datas=[
        ("adbperfetto/web", "adbperfetto/web"),
        ("adbperfetto/presets", "adbperfetto/presets"),
    ],
    hiddenimports=collect_submodules("uvicorn")
                  + collect_submodules("pystray")
                  + ["adbperfetto._buildinfo"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "playwright", "tests", "httpx"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="adbperfetto",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon="adbperfetto/assets/adbperfetto.ico",
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
