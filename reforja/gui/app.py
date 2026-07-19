"""Ponto de entrada da GUI: QApplication, tema e janela principal."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _default_run_dir() -> Path:
    """Diretorio onde os LOGS sao gravados.

    Usa o cwd quando gravavel (paridade com o CLI); senao cai para a home.
    """
    cwd = Path.cwd()
    if os.access(cwd, os.W_OK):
        return cwd
    return Path.home()


def main(argv: list[str] | None = None) -> int:
    # Modo askpass: quando o sudo -A reinvoca o proprio app como helper de senha,
    # mostramos so o dialogo (nunca a GUI principal). Cobre AppImage e fonte.
    if os.environ.get("REFORJA_ASKPASS") == "1":
        from .askpass import run_askpass_dialog

        return run_askpass_dialog()

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
    app.setApplicationName("Reforja Pos-Formatacao")
    app.setApplicationDisplayName("Reforja")
    # Alinha o WM_CLASS ao .desktop (reforja.desktop) para que o KDE Wayland
    # agrupe a janela com o icone correto na barra de tarefas.
    app.setDesktopFileName("reforja")
    # Icone da janela para ambientes sem o .desktop instalado (rodando do fonte).
    icon_path = Path(__file__).resolve().parents[2] / "assets" / "reforja.png"
    if icon_path.exists():
        from PySide6.QtGui import QIcon

        app.setWindowIcon(QIcon(str(icon_path)))
    from . import settings
    from .theme import build_stylesheet

    app.setStyleSheet(build_stylesheet(settings.load().get("theme") == "dark"))

    window = MainWindow(_default_run_dir())
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
