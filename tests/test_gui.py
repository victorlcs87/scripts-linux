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

from reforja.gui import askpass as askpass_mod  # noqa: E402
from reforja.gui.gui_logger import GuiLogger  # noqa: E402
from reforja.gui.main_window import MainWindow, _format_line_html  # noqa: E402
from reforja.gui.updater import _ssl_context, parse_release, running_appimage  # noqa: E402
from reforja.steps import ALL_GROUPS, ALL_STEPS  # noqa: E402


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


def _check(window, step_id: str) -> None:
    window._item_for_step(step_id).setCheckState(Qt.CheckState.Checked)


def test_acao_roda_apenas_etapas_marcadas(app, tmp_path: Path, monkeypatch) -> None:
    window = MainWindow(tmp_path)
    # Evita iniciar threads de verdade: so queremos inspecionar a fila montada.
    monkeypatch.setattr(window, "_next_in_queue", lambda: None)

    _check(window, "03")
    _check(window, "10")
    assert sorted(s.id for s in window._checked_steps()) == ["03", "10"]

    window._run_action("status")
    assert sorted(s.id for s, _ in window._queue) == ["03", "10"]
    assert all(action == "status" for _s, action in window._queue)


def test_gui_nao_tem_botao_dry_run(app, tmp_path: Path) -> None:
    window = MainWindow(tmp_path)
    assert not hasattr(window, "_btn_dry")


def test_askpass_prefere_o_do_sistema(monkeypatch) -> None:
    monkeypatch.delenv("SUDO_ASKPASS", raising=False)
    monkeypatch.setattr(
        askpass_mod.shutil, "which", lambda name: "/usr/bin/ksshaskpass" if name == "ksshaskpass" else None
    )
    assert askpass_mod.resolve_askpass() == "/usr/bin/ksshaskpass"


def test_askpass_self_invocation_nao_reabre_gui(monkeypatch) -> None:
    # Sem askpass do sistema nem kdialog/zenity -> cai na auto-invocacao.
    monkeypatch.delenv("SUDO_ASKPASS", raising=False)
    monkeypatch.setattr(askpass_mod.shutil, "which", lambda _name: None)
    path = askpass_mod.resolve_askpass()
    body = Path(path).read_text()
    assert "REFORJA_ASKPASS=1" in body
    # frozen: chama sys.executable direto; fonte: chama `-m reforja.gui`.
    assert "reforja.gui" in body or askpass_mod.sys.executable in body


def test_run_askpass_dialog_imprime_senha(app, monkeypatch, capsys) -> None:
    from PySide6.QtWidgets import QInputDialog

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("minha-senha", True)))
    rc = askpass_mod.run_askpass_dialog()
    assert rc == 0
    assert capsys.readouterr().out == "minha-senha"


def test_run_askpass_dialog_cancelado(app, monkeypatch, capsys) -> None:
    from PySide6.QtWidgets import QInputDialog

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("", False)))
    rc = askpass_mod.run_askpass_dialog()
    assert rc == 1
    assert capsys.readouterr().out == ""


def test_app_main_askpass_mode_nao_abre_janela(monkeypatch) -> None:
    from reforja.gui import app as app_mod

    monkeypatch.setenv("REFORJA_ASKPASS", "1")
    called: list[bool] = []
    monkeypatch.setattr("reforja.gui.askpass.run_askpass_dialog", lambda: called.append(True) or 0)
    rc = app_mod.main([])
    assert rc == 0
    assert called == [True]


def test_selecionar_etapa_mostra_descricao_no_console(app, tmp_path: Path) -> None:
    window = MainWindow(tmp_path)
    item = window._item_for_step("15")
    window._list.setCurrentItem(item)
    texto = window._console.toPlainText()
    # O console mostra o titulo e a descricao da etapa selecionada.
    assert "Atualizar AppImages" in texto
    assert "GitHub Releases" in texto
    # o tooltip do item tambem tem a descricao
    assert "GitHub Releases" in item.toolTip()


def test_acao_sem_marcacao_usa_destacada(app, tmp_path: Path, monkeypatch) -> None:
    window = MainWindow(tmp_path)
    monkeypatch.setattr(window, "_next_in_queue", lambda: None)

    window._list.setCurrentItem(window._item_for_step("00"))
    window._run_action("status")
    assert [s.id for s, _ in window._queue] == ["00"]


def test_clicar_cabecalho_marca_e_desmarca_grupo(app, tmp_path: Path) -> None:
    window = MainWindow(tmp_path)
    header = next(
        window._list.item(i) for i in range(window._list.count()) if window._list.item(i).text() == "APLICATIVOS"
    )
    window._on_item_clicked(header)
    assert sorted(s.id for s in window._checked_steps()) == ["03", "10", "15"]
    window._on_item_clicked(header)
    assert window._checked_steps() == []


def test_sidebar_tem_cabecalhos_e_todas_as_etapas(app, tmp_path: Path) -> None:
    window = MainWindow(tmp_path)
    steps = [step.id for _item, step in window._step_items()]
    assert sorted(steps) == sorted(s.id for s in ALL_STEPS)
    # total = etapas + um cabecalho por grupo
    assert window._list.count() == len(ALL_STEPS) + len(ALL_GROUPS)


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


def test_ssl_context_disponivel() -> None:
    import ssl

    ctx = _ssl_context()
    # Contexto valido com verificacao habilitada (usa certifi quando presente).
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED


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


# --- seletor multi-item (choose_many) ---------------------------------------------
def _multi_req(options):
    import threading

    return {
        "kind": "multi",
        "prompt": "Quais itens",
        "options": options,
        "detail": None,
        "result": [],
        "event": threading.Event(),
    }


def test_choose_many_retorna_indices_marcados(app, monkeypatch) -> None:
    from PySide6.QtWidgets import QDialog, QListWidget, QWidget

    from reforja.gui.prompts import GuiInteraction

    gi = GuiInteraction(QWidget())

    def fake_exec(dialog):
        listing = dialog.findChild(QListWidget)
        listing.item(0).setCheckState(Qt.CheckState.Checked)
        listing.item(2).setCheckState(Qt.CheckState.Checked)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", fake_exec)
    req = _multi_req(["a", "b", "c"])
    gi._handle_multi(req)
    assert req["result"] == [0, 2]


def test_choose_many_cancelar_retorna_vazio(app, monkeypatch) -> None:
    from PySide6.QtWidgets import QDialog, QWidget

    from reforja.gui.prompts import GuiInteraction

    gi = GuiInteraction(QWidget())
    monkeypatch.setattr(QDialog, "exec", lambda dialog: QDialog.DialogCode.Rejected)
    req = _multi_req(["a", "b"])
    gi._handle_multi(req)
    assert req["result"] == []
