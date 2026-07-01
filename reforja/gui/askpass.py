"""Resolucao de um helper askpass grafico para `sudo -A`.

Ordem de preferencia:
1. $SUDO_ASKPASS (override);
2. askpass do sistema (ksshaskpass no KDE, ssh-askpass, ...);
3. kdialog/zenity (dialogos nativos que ja imprimem a senha no stdout);
4. auto-invocacao: reexecuta o proprio app em "modo askpass" (REFORJA_ASKPASS=1),
   que mostra so o dialogo de senha (nao a GUI). Funciona inclusive no AppImage
   congelado, onde sys.executable e o proprio binario do app.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

# Ordem de preferencia: KDE primeiro, depois alternativas comuns.
_KNOWN_ASKPASS = (
    "ksshaskpass",
    "ssh-askpass",
    "lxqt-openssh-askpass",
    "x11-ssh-askpass",
    "ssh-askpass-fullscreen",
)


def run_askpass_dialog() -> int:
    """Mostra so o dialogo de senha (modo askpass) e imprime a senha no stdout.

    O sudo passa o texto do prompt como argv[1]. Retorna 0 (ok) ou 1 (cancelado).
    """
    from PySide6.QtWidgets import QApplication, QInputDialog, QLineEdit

    app = QApplication.instance() or QApplication(sys.argv)
    _ = app  # mantem a instancia viva
    prompt = sys.argv[1] if len(sys.argv) > 1 else "Senha do sudo:"
    text, ok = QInputDialog.getText(None, "Reforja - autenticacao", prompt, QLineEdit.EchoMode.Password)
    if not ok:
        return 1
    sys.stdout.write(text)
    sys.stdout.flush()
    return 0


def _system_askpass() -> str | None:
    for name in _KNOWN_ASKPASS:
        path = shutil.which(name)
        if path:
            return path
    return None


def _cache_dir() -> Path:
    cache = Path(tempfile.gettempdir()) / "reforja-askpass"
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def _write_wrapper(name: str, body: str) -> str:
    wrapper = _cache_dir() / name
    wrapper.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(wrapper)


def _native_dialog_askpass() -> str | None:
    """Wrapper para kdialog/zenity, que ja imprimem a senha no stdout."""
    kdialog = shutil.which("kdialog")
    if kdialog:
        return _write_wrapper("askpass-kdialog.sh", f'exec "{kdialog}" --password "$1"')
    zenity = shutil.which("zenity")
    if zenity:
        return _write_wrapper("askpass-zenity.sh", f'exec "{zenity}" --password --title "$1"')
    return None


def _self_invocation_askpass() -> str:
    """Reexecuta o proprio app em modo askpass (funciona no AppImage congelado)."""
    if getattr(sys, "frozen", False):
        launcher = f'"{sys.executable}"'
    else:
        launcher = f'"{sys.executable}" -m reforja.gui'
    return _write_wrapper("askpass-self.sh", f'exec env REFORJA_ASKPASS=1 {launcher} "$@"')


def resolve_askpass() -> str | None:
    """Retorna o caminho de um askpass utilizavel, ou None se nada disponivel."""
    override = os.environ.get("SUDO_ASKPASS")
    if override and Path(override).exists():
        return override
    system = _system_askpass()
    if system:
        return system
    native = _native_dialog_askpass()
    if native:
        return native
    try:
        return _self_invocation_askpass()
    except OSError:
        return None
