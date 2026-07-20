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
    # Alem das paginas do menu, ha uma pagina de etapa por step (fora do menu).
    assert window._pages.count() == window._menu.count() + len(ALL_STEPS)
    assert window._menu.item(0).text() == "Inicio"
    assert window._menu.item(window._menu.count() - 1).text() == "Atualizacoes"
    # Trocar de entrada no menu troca a pagina exibida (via mapa row->pagina).
    window._menu.setCurrentRow(2)
    assert window._pages.currentIndex() == window._row_to_page[2]


def test_cartoes_cobrem_todas_as_etapas(app, tmp_path: Path) -> None:
    window = MainWindow(tmp_path)
    assert sorted(window._summary_cards.keys()) == sorted(s.id for s in ALL_STEPS)


def test_abrir_etapa_mostra_pagina_da_etapa(app, tmp_path: Path) -> None:
    window = MainWindow(tmp_path)
    window._open_step_page("10")
    assert window._pages.currentIndex() == window._step_page_index["10"]
    # O grupo dono da etapa 10 continua destacado no menu.
    assert window._menu.currentRow() == window._group_row_of_step["10"]


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
    card = window._summary_cards["15"]
    textos = [child.text() for child in card.findChildren(type(card._status))]
    assert any("Atualizar AppImages" in t for t in textos)
    assert any("GitHub Releases" in t for t in textos)


# --- cards de item (grade estilo Flathub) -----------------------------------------
def _task(state: str, **kwargs):
    from reforja.steps_base import StepTask

    t = StepTask(key=kwargs.get("key", "x"), label=kwargs.get("label", "App"), run=lambda: None)
    t.state = state
    for attr, value in kwargs.items():
        setattr(t, attr, value)
    return t


def test_item_card_instalado_mostra_chip_e_reinstalar(app, tmp_path: Path) -> None:
    from reforja.gui.main_window import ItemCard

    window = MainWindow(tmp_path)
    card = ItemCard(_step("10"), _task("aplicado", detail="flatpak"), window)
    # isVisible() e False em widget nao exibido (offscreen); isHidden() reflete o
    # estado explicito de visibilidade que o card define.
    assert not card._chip.isHidden()
    assert "flatpak" in card._chip.text()
    assert not card._secondary.isHidden() and card._secondary.text() == "Reinstalar"
    # Ja instalado nao oferece o botao primario de instalar.
    assert card._action.isHidden()


def test_item_card_pendente_mostra_instalar(app, tmp_path: Path) -> None:
    from reforja.gui.main_window import ItemCard

    window = MainWindow(tmp_path)
    card = ItemCard(_step("10"), _task("pendente"), window)
    assert not card._action.isHidden() and card._action.text() == "Instalar"
    assert card._chip.isHidden()
    assert card._secondary.isHidden()


def test_item_card_appimage_instalado_diz_atualizar(app, tmp_path: Path) -> None:
    from reforja.gui.main_window import ItemCard

    window = MainWindow(tmp_path)
    card = ItemCard(_step("15"), _task("aplicado"), window)
    assert card._secondary.text() == "Atualizar"


def test_install_item_injeta_selection_e_force(app, tmp_path: Path, monkeypatch) -> None:
    window = MainWindow(tmp_path)
    captured: dict = {}

    def fake_run_action(action, steps, *, selection=None, force=None, on_done=None):
        captured.update(action=action, steps=steps, selection=selection, force=force)

    monkeypatch.setattr(window, "_run_action", fake_run_action)
    step10 = _step("10")
    window._install_item(step10, "Discord", force=True)

    assert captured["action"] == "apply"
    assert captured["steps"] == [step10]
    assert captured["selection"] == {"10": ("Discord",)}
    assert captured["force"] == {"10": frozenset({"Discord"})}


def test_step_worker_recebe_selection_e_force(app, tmp_path: Path, monkeypatch) -> None:
    window = MainWindow(tmp_path)
    captured: dict = {}

    import reforja.gui.main_window as mw

    class _FakeWorker:
        def __init__(self, step_cls, action, logger, **kwargs) -> None:
            captured.update(step_id=step_cls.id, selection=kwargs.get("selection"), force=kwargs.get("force_keys"))

        def start(self) -> None:
            pass

        # sinais usados por _start_worker
        class _S:
            def connect(self, *_a, **_k) -> None:
                pass

        resultReady = _S()
        failed = _S()
        finished = _S()

    monkeypatch.setattr(mw, "StepWorker", _FakeWorker)
    window._selection_map = {"10": ("Discord",)}
    window._force_map = {"10": frozenset({"Discord"})}
    window._start_worker(_step("10"), "apply")

    assert captured["selection"] == ("Discord",)
    assert captured["force"] == frozenset({"Discord"})


def _plan(step_cls, tasks):
    return (step_cls, object(), tasks)


def test_previa_marca_o_que_falta_e_devolve_selecao(app, tmp_path: Path) -> None:
    from reforja.gui.main_window import BatchPreviewDialog

    window = MainWindow(tmp_path)
    tasks = [_task("aplicado", key="A", label="A"), _task("pendente", key="B", label="B")]
    dialog = BatchPreviewDialog([_plan(_step("10"), tasks)], window, title="Aplicar")
    # Vem marcado o que falta (B), nao o ja instalado (A).
    marcados = {key: check.isChecked() for _sid, key, _state, check in dialog._checks}
    assert marcados == {"A": False, "B": True}
    selection, force = dialog.result_selection()
    assert selection == {"10": ("B",)}
    assert force == {}  # B nao estava instalado -> nao forca


def test_previa_marcar_instalado_forca_reinstalar(app, tmp_path: Path) -> None:
    from reforja.gui.main_window import BatchPreviewDialog

    window = MainWindow(tmp_path)
    tasks = [_task("aplicado", key="A", label="A")]
    dialog = BatchPreviewDialog([_plan(_step("10"), tasks)], window, title="Aplicar")
    # Marcar um ja instalado = reinstalar -> entra na selecao E no force.
    dialog._checks[0][3].setChecked(True)
    selection, force = dialog.result_selection()
    assert selection == {"10": ("A",)}
    assert force == {"10": frozenset({"A"})}


def test_previa_toda_etapa_recebe_selecao_explicita(app, tmp_path: Path) -> None:
    from reforja.gui.main_window import BatchPreviewDialog

    window = MainWindow(tmp_path)
    # Duas etapas na previa; nenhuma marcada -> ambas recebem () (sem cair no modal).
    d = BatchPreviewDialog(
        [_plan(_step("10"), [_task("aplicado", key="A")]), _plan(_step("14"), [_task("aplicado", key="B")])],
        window,
        title="Aplicar tudo",
    )
    d._check_none()
    selection, _force = d.result_selection()
    assert selection == {"10": (), "14": ()}


def test_apply_em_lote_passa_pela_previa(app, tmp_path: Path, monkeypatch) -> None:
    window = MainWindow(tmp_path)
    chamado: dict = {}
    monkeypatch.setattr(window, "_preview_then_apply", lambda steps, on_done: chamado.update(steps=steps))
    started = {}
    monkeypatch.setattr(window, "_run_steps", lambda *a, **k: started.update(ran=True))

    window._run_action("apply", [_step("10"), _step("14")])
    # Apply sem selecao -> vai para a previa, nao direto para _run_steps.
    assert chamado.get("steps") == [_step("10"), _step("14")]
    assert "ran" not in started


def test_apply_com_selecao_explicita_pula_previa(app, tmp_path: Path, monkeypatch) -> None:
    window = MainWindow(tmp_path)
    monkeypatch.setattr(window, "_preview_then_apply", lambda *a, **k: pytest.fail("nao deveria abrir a previa"))
    ran = {}
    monkeypatch.setattr(window, "_run_steps", lambda *a, **k: ran.update(ok=True))
    # Instalacao por card injeta selection -> deve pular a previa.
    window._run_action("apply", [_step("10")], selection={"10": ("Discord",)})
    assert ran.get("ok") is True


def test_item_card_removivel_mostra_botao_remover(app, tmp_path: Path) -> None:
    from reforja.gui.main_window import ItemCard

    window = MainWindow(tmp_path)
    task = _task("aplicado", key="Discord", label="Discord")
    task.remove = lambda: None  # tarefa que sabe se remover
    card = ItemCard(_step("10"), task, window)
    assert not card._remove.isHidden() and card._remove.text() == "Remover"
    # Sem callable de remove, o botao nao aparece.
    card2 = ItemCard(_step("10"), _task("aplicado", key="X"), window)
    assert card2._remove.isHidden()


def test_remove_item_injeta_acao_remove(app, tmp_path: Path, monkeypatch) -> None:
    window = MainWindow(tmp_path)
    import reforja.gui.main_window as mw

    monkeypatch.setattr(mw.QMessageBox, "question", staticmethod(lambda *a, **k: mw.QMessageBox.StandardButton.Yes))
    captured: dict = {}
    monkeypatch.setattr(window, "_run_action", lambda action, steps, **kw: captured.update(action=action, **kw))
    window._remove_item(_step("10"), "Discord", "Discord")
    assert captured["action"] == "remove"
    assert captured["selection"] == {"10": ("Discord",)}


def test_remove_item_cancelado_nao_faz_nada(app, tmp_path: Path, monkeypatch) -> None:
    window = MainWindow(tmp_path)
    import reforja.gui.main_window as mw

    monkeypatch.setattr(mw.QMessageBox, "question", staticmethod(lambda *a, **k: mw.QMessageBox.StandardButton.No))
    chamou = []
    monkeypatch.setattr(window, "_run_action", lambda *a, **k: chamou.append(True))
    window._remove_item(_step("10"), "Discord", "Discord")
    assert chamou == []


def test_card_estados_busy_e_erro(app, tmp_path: Path) -> None:
    from reforja.gui.main_window import ItemCard

    window = MainWindow(tmp_path)
    card = ItemCard(_step("10"), _task("pendente", key="X"), window)
    card.set_busy("Instalando...")
    assert "Instalando" in card._chip.text() and not card._chip.isHidden()
    card.set_error("deu ruim")
    assert card._action.text() == "Repetir" and card._chip.text().startswith("⚠")
    assert card._chip.toolTip() == "deu ruim"


def test_preset_abre_previa_com_selecao(app, tmp_path: Path, monkeypatch) -> None:
    window = MainWindow(tmp_path)
    captured: dict = {}
    monkeypatch.setattr(
        window,
        "_preview_then_apply",
        lambda steps, on_done, *, initial_selection=None, title=None: captured.update(
            steps=[s.id for s in steps], sel=initial_selection, title=title
        ),
    )
    window._apply_preset("Comunicacao")
    # Comunicacao mexe so na etapa 10, com Discord/TeamSpeak/ZapZap.
    assert captured["steps"] == ["10"]
    assert captured["sel"] == {"10": ("Discord", "TeamSpeak", "ZapZap")}
    assert "Comunicacao" in captured["title"]


def test_previa_com_initial_selection_marca_o_preset(app, tmp_path: Path) -> None:
    from reforja.gui.main_window import BatchPreviewDialog

    window = MainWindow(tmp_path)
    tasks = [_task("pendente", key="Discord"), _task("pendente", key="Steam")]
    dialog = BatchPreviewDialog(
        [_plan(_step("10"), tasks)], window, title="Perfil", initial_selection={"10": ("Discord",)}
    )
    marcados = {key: chk.isChecked() for _sid, key, _st, chk in dialog._checks}
    assert marcados == {"Discord": True, "Steam": False}


def test_toggle_tema_persiste_e_troca(app, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from reforja.gui import settings

    window = MainWindow(tmp_path)
    assert settings.load()["theme"] == "light"
    window._toggle_theme()
    assert settings.load()["theme"] == "dark"
    window._toggle_theme()
    assert settings.load()["theme"] == "light"


def test_toggle_console_persiste(app, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg2"))
    from reforja.gui import settings

    window = MainWindow(tmp_path)
    window._toggle_console()
    assert settings.load()["console_collapsed"] is True
    assert window._console_collapsed is True


def test_theme_dark_stylesheet_difere(app) -> None:
    from reforja.gui import theme

    claro = theme.build_stylesheet(False)
    escuro = theme.build_stylesheet(True)
    assert claro != escuro
    # compliance() usa a paleta ativa: apos build dark, a cor muda.
    theme.build_stylesheet(True)
    _glyph, cor_dark = theme.compliance("aplicado")
    theme.build_stylesheet(False)
    _glyph, cor_light = theme.compliance("aplicado")
    assert cor_dark != cor_light


def test_busca_filtra_cards(app, tmp_path: Path) -> None:
    window = MainWindow(tmp_path)
    page = window._step_pages["10"]  # Apps: 15 itens, varias categorias -> tem filtro
    page.show()
    _qt_app = QApplication.instance()
    if not page._built:
        page._build_cards()
        page._built = True
    assert page._filter_bar is not None  # a barra aparece na etapa 10
    page._on_search("discord")
    visiveis = [c.key for c in page._visible_cards]
    assert visiveis == ["Discord"]
    page._on_search("")
    assert len(page._visible_cards) == len(page._cards)


def test_filtro_categoria(app, tmp_path: Path) -> None:
    window = MainWindow(tmp_path)
    page = window._step_pages["10"]
    page.show()
    if not page._built:
        page._build_cards()
        page._built = True
    page._on_category("comunicacao")
    cats = {c._task.category for c in page._visible_cards}
    assert cats == {"comunicacao"}


def test_installed_label_encurta_origem(app) -> None:
    from reforja.gui.main_window import _installed_label

    t = _task("aplicado", key="x", detail="instalado via flatpak (com.x)")
    assert _installed_label(t) == "flatpak"
    t2 = _task("aplicado", key="y", detail="instalado: 1.2.3")
    assert _installed_label(t2) == "1.2.3"


def test_icone_fallback_tipografico_sem_rede(app) -> None:
    from reforja.gui import icons

    # Sem asset local nem app_id -> avatar tipografico (nunca chama a rede).
    pix = icons.resolve_icon("Discord", "com.discordapp.Discord", "comunicacao", 48)
    assert not pix.isNull()
    assert pix.width() == 48 and pix.height() == 48


def test_flathub_targets_ignora_caminhos_locais(app) -> None:
    from reforja.gui import icons

    tarefas = [
        _task("pendente", key="a", icon="com.discordapp.Discord"),
        _task("pendente", key="b", icon="assets/reforja.png"),
        _task("pendente", key="c", icon=""),
    ]
    alvos = icons.flathub_icon_targets(tarefas)
    assert alvos == [("a", "com.discordapp.Discord")]


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


def test_pagina_da_etapa_tem_desfazer_so_quando_ha_undo(app, tmp_path: Path) -> None:
    from PySide6.QtWidgets import QPushButton

    window = MainWindow(tmp_path)
    rotulos_14 = {b.text() for b in window._step_pages["14"].findChildren(QPushButton)}
    rotulos_10 = {b.text() for b in window._step_pages["10"].findChildren(QPushButton)}
    assert "Desfazer" in rotulos_14
    assert "Desfazer" not in rotulos_10


def test_undo_pede_confirmacao(app, tmp_path: Path, monkeypatch) -> None:
    import reforja.gui.main_window as mw

    window = MainWindow(tmp_path)
    monkeypatch.setattr(window, "_run_steps", lambda *a, **k: None)
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


# --- icones das tarefas (config steps sem id Flathub proprio) ----------------------
def test_task_theme_icons_cobrem_tarefas_sem_icone_proprio() -> None:
    """Toda tarefa sem `icon` proprio (nem app_id Flathub nem asset) precisa de um
    icone de tema mapeado, para nunca cair no avatar de letra."""
    import tempfile

    from reforja.core import Logger, Runner, detect_user
    from reforja.gui.icons import TASK_THEME_ICONS
    from reforja.steps import ALL_STEPS
    from reforja.steps_base import StepContext

    d = Path(tempfile.mkdtemp())
    logger = Logger(d, "test")
    ctx = StepContext(root=d, run_dir=d, user=detect_user(), logger=logger, runner=Runner(logger, dry_run=True))
    sem_mapeamento: list[str] = []
    for cls in ALL_STEPS:
        for task in cls(ctx).tasks():
            if getattr(task, "icon", ""):
                continue
            if (cls.id, task.key) not in TASK_THEME_ICONS:
                sem_mapeamento.append(f"{cls.id}:{task.key}")
    assert not sem_mapeamento, f"tarefas sem icone: {sem_mapeamento}"


def test_resolve_task_icon_usa_mapa_de_tema(app) -> None:
    """resolve_task_icon injeta o icone de tema mapeado para tarefas sem icone proprio."""
    from reforja.gui import icons

    task = _task("pendente", key="fstab", label="Montagens", category="")
    task.icon = ""
    pix = icons.resolve_task_icon("08", task, 48)
    assert not pix.isNull()


def test_relaunch_appimage_fora_de_appimage_retorna_false(monkeypatch) -> None:
    from reforja.gui.updater import relaunch_appimage

    monkeypatch.delenv("APPIMAGE", raising=False)
    assert relaunch_appimage("1.0.9") is False


def test_relaunch_appimage_lanca_processo_com_tag(tmp_path: Path, monkeypatch) -> None:
    import subprocess as _sp

    from reforja.gui import updater

    fake = tmp_path / "Reforja.AppImage"
    fake.write_bytes(b"x")
    monkeypatch.setenv("APPIMAGE", str(fake))
    monkeypatch.setenv("APPDIR", "/tmp/mount-antigo")
    calls: list[tuple[list[str], dict]] = []
    monkeypatch.setattr(_sp, "Popen", lambda cmd, **kw: calls.append((cmd, kw)))

    assert updater.relaunch_appimage("1.0.9") is True
    cmd, kwargs = calls[0]
    assert cmd == [str(fake)]
    assert kwargs["env"][updater.UPDATED_ENV] == "1.0.9"
    # variaveis do runtime antigo nao vazam para a nova instancia
    assert "APPDIR" not in kwargs["env"] and "APPIMAGE" not in kwargs["env"]


def test_announce_update_done_avisa_e_limpa_env(tmp_path: Path, monkeypatch) -> None:
    import reforja.gui.main_window as mw
    from reforja.gui.main_window import MainWindow
    from reforja.gui.updater import UPDATED_ENV

    shown: list[str] = []
    monkeypatch.setattr(mw.QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a[2])))
    monkeypatch.setattr(mw.QTimer, "singleShot", staticmethod(lambda _ms, fn: fn()))
    monkeypatch.setenv(UPDATED_ENV, "1.0.9")

    MainWindow(tmp_path)
    assert shown and "v1.0.9" in shown[0]
    assert UPDATED_ENV not in os.environ
