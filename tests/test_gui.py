"""Testes do frontend grafico. Pulam quando PySide6 nao esta instalado, para
nao quebrar o gate basico de CI; o job de release valida com PySide6 presente.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Nao inicia a checagem de atualizacao (thread de rede) durante os testes.
os.environ["REFORJA_NO_UPDATE_CHECK"] = "1"

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from reforja.gui.gui_logger import GuiLogger  # noqa: E402
from reforja.gui.main_window import MainWindow, _format_line_html  # noqa: E402
from reforja.gui.updater import parse_release, running_appimage  # noqa: E402
from reforja.steps import ALL_STEPS  # noqa: E402


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


def test_batch_executa_apenas_etapas_marcadas(app, tmp_path: Path, monkeypatch) -> None:
    window = MainWindow(tmp_path)
    # Evita iniciar threads de verdade: so queremos inspecionar a fila montada.
    monkeypatch.setattr(window, "_next_in_queue", lambda: None)

    window._list.item(2).setCheckState(Qt.CheckState.Checked)
    window._list.item(4).setCheckState(Qt.CheckState.Checked)
    assert [s.id for s in window._checked_steps()] == [ALL_STEPS[2].id, ALL_STEPS[4].id]

    window._run_batch("dry-run")
    assert [s.id for s, _ in window._queue] == [ALL_STEPS[2].id, ALL_STEPS[4].id]
    assert all(action == "dry-run" for _s, action in window._queue)


def test_batch_sem_marcacao_roda_todas(app, tmp_path: Path, monkeypatch) -> None:
    window = MainWindow(tmp_path)
    monkeypatch.setattr(window, "_next_in_queue", lambda: None)

    window._run_batch("status")
    assert len(window._queue) == len(ALL_STEPS)


def test_marcar_todas_e_limpar(app, tmp_path: Path) -> None:
    window = MainWindow(tmp_path)
    window._set_all_checked(True)
    assert len(window._checked_steps()) == len(ALL_STEPS)
    window._set_all_checked(False)
    assert window._checked_steps() == []


# --- atualizacao do app -----------------------------------------------------------
def test_parse_release_available_e_current() -> None:
    data = {
        "tag_name": "v1.0.9",
        "assets": [
            {"name": "SHA256SUMS"},
            {"name": "Reforja-1.0.9-x86_64.AppImage", "browser_download_url": "http://x/Reforja.AppImage"},
        ],
    }
    status, tag, url = parse_release(data, "1.0.5")
    assert status == "available"
    assert tag == "1.0.9"
    assert url == "http://x/Reforja.AppImage"
    # Mesma versao (com/sem 'v') => current
    assert parse_release({"tag_name": "v1.0.5", "assets": []}, "1.0.5")[0] == "current"


def test_running_appimage_env(monkeypatch) -> None:
    monkeypatch.delenv("APPIMAGE", raising=False)
    assert running_appimage() is None
    monkeypatch.setenv("APPIMAGE", "/home/u/Reforja-x86_64.AppImage")
    assert running_appimage() == Path("/home/u/Reforja-x86_64.AppImage")


class _FakeSignal:
    def connect(self, *_args, **_kwargs) -> None:
        pass


def test_offer_update_inicia_download_quando_em_appimage(app, tmp_path: Path, monkeypatch) -> None:
    window = MainWindow(tmp_path)
    started: dict[str, object] = {}

    class _FakeDownload:
        def __init__(self, url, target) -> None:
            started["url"] = url
            started["target"] = target

        finished = _FakeSignal()

        def start(self) -> None:
            started["started"] = True

    import reforja.gui.main_window as mw

    monkeypatch.setattr(mw, "DownloadWorker", _FakeDownload)
    monkeypatch.setattr(mw.QMessageBox, "question", staticmethod(lambda *a, **k: mw.QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(mw, "running_appimage", lambda: Path("/home/u/Reforja.AppImage"))

    window._offer_update("1.0.9", "http://x/Reforja.AppImage")

    assert started.get("started") is True
    assert started["target"] == Path("/home/u/Reforja.AppImage")
    assert window._updating is True


def test_offer_update_sem_appimage_abre_pagina(app, tmp_path: Path, monkeypatch) -> None:
    window = MainWindow(tmp_path)
    import reforja.gui.main_window as mw

    monkeypatch.setattr(mw.QMessageBox, "question", staticmethod(lambda *a, **k: mw.QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(mw.QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(mw, "running_appimage", lambda: None)
    opened: list[object] = []
    monkeypatch.setattr(mw.QDesktopServices, "openUrl", staticmethod(lambda url: opened.append(url)))

    window._offer_update("1.0.9", "http://x/page")

    assert opened, "deveria abrir a pagina de download como fallback"
    assert window._updating is False
