# -*- mode: python ; coding: utf-8 -*-
# Build: pyinstaller droidctrl.spec

block_cipher = None

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'flask',
        'werkzeug',
        'werkzeug.urls',
        'werkzeug.serving',
        'werkzeug.routing',
        'werkzeug.routing.map',
        'werkzeug.routing.rules',
        'werkzeug.middleware',
        'werkzeug.middleware.dispatcher',
        'jinja2',
        'click',
        'appium',
        'appium.webdriver',
        'appium.options',
        'appium.options.android',
        'appium.webdriver.common.appiumby',
        'cryptography',
        'cryptography.hazmat.primitives.asymmetric.x25519',
        'cryptography.hazmat.primitives.serialization',
        'queue',
        'webview',
        'clr',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'tkinter', 'matplotlib'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='DroidCtrl',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,      # no console window — app opens in browser
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon='icon.ico',  # uncomment and add icon.ico to use a custom icon
)
