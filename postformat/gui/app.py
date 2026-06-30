"""Ponto de entrada da GUI: QApplication, tema e janela principal."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_THEME = Path(__file__).with_name("theme.qss")


def _default_run_dir() -> Path:
    """Diretorio onde os LOGS sao gravados.

    Usa o cwd quando gravavel (paridade com o CLI); senao cai para a home.
    """
    cwd = Path.cwd()
    if os.access(cwd, os.W_OK):
        return cwd
    return Path.home()


def main(argv: list[str] | None = None) -> int:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        sys.stderr.write(
            "PySide6 nao esta instalado. Instale com `pip install PySide6` "
            "ou rode `python 00-pos-formatacao-cachyos.py` para preparar as dependencias.\n"
        )
        return 1

    # Importacao tardia: so depende de PySide6 apos confirmar que existe.
    from .main_window import MainWindow

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("Sisteminha Pos-Formatacao")
    app.setApplicationDisplayName("Sisteminha")
    # Alinha o WM_CLASS ao .desktop (sisteminha.desktop) para que o KDE Wayland
    # agrupe a janela com o icone correto na barra de tarefas.
    app.setDesktopFileName("sisteminha")
    if _THEME.exists():
        app.setStyleSheet(_THEME.read_text(encoding="utf-8"))

    window = MainWindow(_default_run_dir())
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
