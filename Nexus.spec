# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['launch.py'],
    pathex=[],
    binaries=[],
    datas=[('frontend', 'frontend'), ('backend', 'backend')],
    hiddenimports=['backend.app', 'backend.database', 'flask', 'flask_cors', 'pdfplumber', 'fitz', 'pandas', 'openpyxl', 'rapidfuzz', 'reportlab'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Nexus',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
