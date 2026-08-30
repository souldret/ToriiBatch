# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=[
        ("assets/icon.ico", "assets"),
        ("assets/check.svg", "assets"),
        ("config/default_config.json", "config"),
    ] + collect_data_files("qtawesome"),
    hiddenimports=["PyQt6", "aiohttp", "natsort", "cryptography", "qtawesome"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["requests"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ToriiBatch",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon="assets/icon.ico",
)
