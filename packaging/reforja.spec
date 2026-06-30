# -*- mode: python ; coding: utf-8 -*-
"""Spec do PyInstaller para o reforja (GUI).

Empacota o pacote reforja (incluindo o tema da GUI e os assets) num
diretorio --onedir, que o build-appimage.sh transforma em AppDir/AppImage.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent

datas = [
    (str(ROOT / "reforja" / "gui" / "theme.qss"), "reforja/gui"),
    (str(ROOT / "assets"), "assets"),
    (str(ROOT / "scripts"), "scripts"),
]

# Garante que todos os steps (carregados dinamicamente) entrem no bundle.
# certifi entra explicitamente para o seu hook embutir o cacert.pem (HTTPS no AppImage).
hidden = collect_submodules("reforja") + ["certifi"]

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "InquirerPy", "prompt_toolkit"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="reforja",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="reforja",
)
