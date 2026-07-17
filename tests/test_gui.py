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
from reforja.gui import theme  # noqa: E402
from reforja.gui.gui_logger import GuiLogger  # noqa: E402
from reforja.gui.main_window import MainWindow, _format_line_html, _has_undo  # noqa: E402
from reforja.gui.updater import _ssl_context, parse_release, running_appimage  # noqa: E402
from reforja.steps import ALL_GROUPS, ALL_STEPS  # noqa: E402


def _step(step_id: str) -> type:
    return next(s for s in ALL_STEPS if s.id == step_id)


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
    assert f"color:{theme.BADGE_COLORS['done']}" in _format_line_html("[done] concluido")
    assert f"color:{theme.BADGE_COLORS['failed']}" in _format_line_html("[failed] falhou")
    assert f"color:{theme.CONSOLE_CMD_COLOR}" in _format_line_html("$ pacman -Syu")
    # Escapa HTML do conteudo
    assert "&lt;tag&gt;" in _format_line_html("[info] <tag>")


def test_navegacao_menu_tem_inicio_grupos_e_atualizacoes(app, tmp_path: Path) -> None:
    window = MainWindow(tmp_path)
    # Inicio + um por grupo + Atualizacoes.
    assert window._menu.count() == len(ALL_GROUPS) + 2
    assert window._pages.count() == window._menu.count()
    assert window._menu.item(0).text() == "Inicio"
    assert window._menu.item(window._menu.count() - 1).text() == "Atualizacoes"
    # Trocar de entrada no menu troca a pagina exibida.
    window._menu.setCurrentRow(2)
    assert window._pages.currentIndex() == 2


def test_cartoes_cobrem_todas_as_etapas(app, tmp_path: Path) -> None:
    window = MainWindow(tmp_path)
    assert sorted(window._cards.keys()) == sorted(s.id for s in ALL_STEPS)


def test_aplicar_tudo_enfileira_todas_as_etapas(app, tmp_path: Path, monkeypatch) -> None:
    window = MainWindow(tmp_path)
    monkeypatch.setattr(window, "_next_in_queue", lambda: None)
    window._run_action("status", list(ALL_STEPS))
    assert sorted(s.id for s, _ in window._queue) == sorted(s.id for s in ALL_STEPS)
    assert all(action == "status" for _s, action in window._queue)


def test_acao_em_uma_etapa_enfileira_so_ela(app, tmp_path: Path, monkeypatch) -> None:
    window = MainWindow(tmp_path)
    monkeypatch.setattr(window, "_next_in_queue", lambda: None)
    window._run_action("status", [_step("00")])
    assert [s.id for s, _ in window._queue] == ["00"]


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


def test_cartao_mostra_titulo_e_descricao_da_etapa(app, tmp_path: Path) -> None:
    window = MainWindow(tmp_path)
    card = window._cards["15"]
    textos = [child.text() for child in card.findChildren(type(card._status))]
    assert any("Atualizar AppImages" in t for t in textos)
    assert any("GitHub Releases" in t for t in textos)


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
        def __init__(self, url, target, sha256_url="") -> None:
            started["url"] = url
            started["target"] = target
            started["sha256_url"] = sha256_url

        finished = _FakeSignal()
        progress = _FakeSignal()

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
def _multi_req(options, preselected=()):
    import threading

    return {
        "kind": "multi",
        "prompt": "Quais itens",
        "options": options,
        "detail": None,
        "preselected": list(preselected),
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


def test_choose_many_marca_os_preselecionados(app, monkeypatch) -> None:
    """O que ja esta configurado (ex.: discos ja no fstab) vem marcado, para
    reaplicar sem precisar remarcar tudo."""
    from PySide6.QtWidgets import QDialog, QListWidget, QWidget

    from reforja.gui.prompts import GuiInteraction

    gi = GuiInteraction(QWidget())
    estados: list[Qt.CheckState] = []

    def fake_exec(dialog):
        listing = dialog.findChild(QListWidget)
        estados.extend(listing.item(index).checkState() for index in range(listing.count()))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", fake_exec)
    req = _multi_req(["a", "b", "c"], preselected=[1])
    gi._handle_multi(req)

    assert estados == [Qt.CheckState.Unchecked, Qt.CheckState.Checked, Qt.CheckState.Unchecked]
    assert req["result"] == [1]


def test_choose_many_cancelar_retorna_vazio(app, monkeypatch) -> None:
    from PySide6.QtWidgets import QDialog, QWidget

    from reforja.gui.prompts import GuiInteraction

    gi = GuiInteraction(QWidget())
    monkeypatch.setattr(QDialog, "exec", lambda dialog: QDialog.DialogCode.Rejected)
    req = _multi_req(["a", "b"])
    gi._handle_multi(req)
    assert req["result"] == []


# --- fase 4: falhas no resumo, Parar, Undo, SHA256 --------------------------------
def test_falha_no_lote_entra_no_resumo_e_bloqueia_restantes(app, tmp_path: Path, monkeypatch) -> None:
    window = MainWindow(tmp_path)
    monkeypatch.setattr(window, "_start_worker", lambda step, action: None)

    window._run_action("status", [_step("00"), _step("03"), _step("10")])  # monta a fila e "inicia" o primeiro (00)

    # O primeiro step (00) falha: vira resultado sintetico e o resto e bloqueado.
    window._on_failed("failed", "explodiu")

    statuses = [(r.step_id, r.status) for r in window._results]
    assert ("00", "failed") in statuses
    assert ("03", "blocked") in statuses
    assert ("10", "blocked") in statuses
    assert window._queue == []


def test_botao_parar_bloqueia_fila_e_habilita_so_em_execucao(app, tmp_path: Path, monkeypatch) -> None:
    window = MainWindow(tmp_path)
    assert window._btn_stop.isEnabled() is False
    window._set_running(True)
    assert window._btn_stop.isEnabled() is True

    window._queue = [(ALL_STEPS[0], "apply"), (ALL_STEPS[1], "apply")]
    window._stop_requested()
    assert window._queue == []
    assert all(r.status == "blocked" for r in window._results)


def test_undo_disponivel_so_para_etapas_que_desfazem() -> None:
    # HardwareStep (14) tem undo; AppsStep (10) usa o placeholder da base (sem undo).
    assert _has_undo(_step("14")) is True
    assert _has_undo(_step("10")) is False


def test_cartao_tem_desfazer_so_quando_ha_undo(app, tmp_path: Path) -> None:
    from PySide6.QtWidgets import QPushButton

    window = MainWindow(tmp_path)
    rotulos_14 = {b.text() for b in window._cards["14"].findChildren(QPushButton)}
    rotulos_10 = {b.text() for b in window._cards["10"].findChildren(QPushButton)}
    assert "Desfazer" in rotulos_14
    assert "Desfazer" not in rotulos_10


def test_undo_pede_confirmacao(app, tmp_path: Path, monkeypatch) -> None:
    import reforja.gui.main_window as mw

    window = MainWindow(tmp_path)
    monkeypatch.setattr(window, "_run_steps", lambda action, steps: None)
    asked: list[str] = []
    monkeypatch.setattr(
        mw.QMessageBox,
        "question",
        staticmethod(lambda *a, **k: asked.append("q") or mw.QMessageBox.StandardButton.No),
    )

    window._run_action("undo", [_step("14")])
    assert asked == ["q"]  # perguntou e o usuario recusou -> nada roda


def test_expected_sha256_encontra_hash_do_arquivo() -> None:
    from reforja.gui.updater import expected_sha256, find_sha256_url

    sums = "abc123  Reforja-1.0.9-x86_64.AppImage\ndef456  Reforja.zsync\n"
    assert expected_sha256(sums, "Reforja-1.0.9-x86_64.AppImage") == "abc123"
    assert expected_sha256(sums, "inexistente") == ""

    data = {
        "assets": [
            {"name": "SHA256SUMS", "browser_download_url": "http://x/SHA256SUMS"},
            {"name": "Reforja.AppImage", "browser_download_url": "http://x/app"},
        ]
    }
    assert find_sha256_url(data) == "http://x/SHA256SUMS"
    assert find_sha256_url({"assets": []}) == ""


def test_download_worker_rejeita_hash_divergente(tmp_path: Path, monkeypatch) -> None:
    from reforja.gui.updater import DownloadWorker

    target = tmp_path / "Reforja.AppImage"
    target.write_bytes(b"antigo")
    worker = DownloadWorker("http://x/Reforja.AppImage", target, "http://x/SHA256SUMS")

    class _Resp:
        headers = {"Content-Length": "4"}

        def __init__(self, payload: bytes) -> None:
            self._payload = payload

        def read(self, _n: int = -1) -> bytes:
            data, self._payload = self._payload, b""
            return data

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(worker, "_expected_hash", lambda: "hash-que-nao-bate")
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, **k: _Resp(b"novo"))
    results: list[tuple[bool, str]] = []
    worker.finished.connect(lambda ok, msg: results.append((ok, msg)))

    worker.run()  # roda sincrono (sem thread) para o teste

    assert results and results[0][0] is False
    assert "SHA256" in results[0][1]
    assert target.read_bytes() == b"antigo"  # binario preservado
