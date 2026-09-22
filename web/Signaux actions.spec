# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['dashboard_app.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['AppKit', 'WebKit', 'Foundation', 'objc'],
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
    [],
    exclude_binaries=True,
    name='Signaux actions',
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
    icon=['dashboard.icns'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Signaux actions',
)
app = BUNDLE(
    coll,
    name='Signaux actions.app',
    icon='dashboard.icns',
    bundle_identifier='com.mathis.signauxactions',
)
