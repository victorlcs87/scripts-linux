"""Testes do frontend grafico. Pulam quando PySide6 nao esta instalado, para
nao quebrar o gate basico de CI; o job de release valida com PySide6 presente.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from postformat.gui.gui_logger import GuiLogger  # noqa: E402
from postformat.gui.main_window import _format_line_html  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


def test_gui_logger_emite_sinal_e_grava_arquivo(app, tmp_path: Path) -> None:
    logger = GuiLogger(tmp_path, "gui-test")
    recebidos: list[str] = []
    logger.signals.output.connect(recebidos.append)
    logger.write("\033[1;38;5;48m[done]\033[0m tudo certo")
    # Sinal chega sem ANSI...
    assert recebidos == ["[done] tudo certo"]
    # ...e o arquivo de log tambem registra a linha (sem ANSI).
    assert "[done] tudo certo" in logger.path.read_text(encoding="utf-8")


def test_gui_logger_transient(app, tmp_path: Path) -> None:
    logger = GuiLogger(tmp_path, "gui-test")
    transitorios: list[str] = []
    limpezas: list[int] = []
    logger.signals.transient.connect(transitorios.append)
    logger.signals.clearTransient.connect(lambda: limpezas.append(1))
    logger.transient("[rodando] etapa")
    logger.clear_transient()
    assert transitorios == ["[rodando] etapa"]
    assert limpezas == [1]


def test_format_line_html_colore_badges() -> None:
    assert "color:#2ee06a" in _format_line_html("[done] concluido")
    assert "color:#ff5f5f" in _format_line_html("[failed] falhou")
    assert "color:#5fe1ff" in _format_line_html("$ pacman -Syu")
    # Escapa HTML do conteudo
    assert "&lt;tag&gt;" in _format_line_html("[info] <tag>")
