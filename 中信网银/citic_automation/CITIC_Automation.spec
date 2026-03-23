# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('config.ini', '.'), ('../../keyboard', 'keyboard'), ('logger.py', '.'), ('config_loader.py', '.'), ('browser.py', '.'), ('statement_collector.py', '.'), ('receipt_collector.py', '.'), ('run_recorder.py', '.')]
binaries = []
hiddenimports = ['playwright', 'loguru', 'yaml', 'pyautogui', 'pygetwindow', 'pyscreeze', 'pymsgbox', 'pytweening', 'mouseinfo', 'rubicon-objc']
tmp_ret = collect_all('playwright')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
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
    name='CITIC_Automation',
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
    icon='NONE',
)
