"""Resolucao de um helper askpass grafico para `sudo -A`.

Prioriza binarios de askpass do sistema (ksshaskpass no KDE etc.). Se nenhum
existir, gera um helper Qt autocontido que reaproveita o mesmo interpretador
Python da aplicacao (funciona inclusive dentro do AppImage).
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

_FALLBACK_SCRIPT = """\
import sys
from PySide6.QtWidgets import QApplication, QInputDialog, QLineEdit

app = QApplication(sys.argv)
prompt = sys.argv[1] if len(sys.argv) > 1 else "Senha:"
text, ok = QInputDialog.getText(None, "Reforja - autenticacao", prompt, QLineEdit.EchoMode.Password)
if not ok:
    sys.exit(1)
sys.stdout.write(text)
sys.exit(0)
"""


def _system_askpass() -> str | None:
    for name in _KNOWN_ASKPASS:
        path = shutil.which(name)
        if path:
            return path
    return None


def _build_fallback() -> str:
    cache = Path(tempfile.gettempdir()) / "reforja-askpass"
    cache.mkdir(parents=True, exist_ok=True)
    script = cache / "_askpass_dialog.py"
    script.write_text(_FALLBACK_SCRIPT, encoding="utf-8")
    wrapper = cache / "askpass.sh"
    wrapper.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(wrapper)


def resolve_askpass() -> str | None:
    """Retorna o caminho de um askpass utilizavel, ou None se nada disponivel."""
    override = os.environ.get("SUDO_ASKPASS")
    if override and Path(override).exists():
        return override
    system = _system_askpass()
    if system:
        return system
    try:
        return _build_fallback()
    except OSError:
        return None
