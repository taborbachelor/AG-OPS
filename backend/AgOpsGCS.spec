# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the single-file Ag-Ops GCS exe.
# Build:  cd backend && venv\Scripts\pyinstaller.exe AgOpsGCS.spec
# Output: backend\dist\AgOpsGCS.exe  (ship with a sitl\ folder next to it for demo mode)
#
# Requires frontend\build to exist first:  cd frontend && npm run build
from PyInstaller.utils.hooks import collect_submodules

# uvicorn and pymavlink both import modules dynamically at runtime; PyInstaller's
# static analysis misses them without these.
hiddenimports = (
    collect_submodules('uvicorn')
    + collect_submodules('pymavlink.dialects.v20')
    + collect_submodules('pymavlink.dialects.v10')
)

a = Analysis(
    ['run_gcs.py'],
    pathex=[],
    binaries=[],
    datas=[('../frontend/build', 'frontend_build')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='AgOpsGCS',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # keep the server log visible; flip to False for a silent app feel
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
