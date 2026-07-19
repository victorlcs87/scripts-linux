"""Driver funcional da GUI do Reforja, ponta a ponta, contra um sistema real.

Exercita TUDO pela GUI (nao pela TUI): constroi a janela, navega por todas as
secoes, abre a pagina de cada etapa e monta os cards, sonda o estado real da
maquina, monta a previa consolidada e roda cada etapa pelo mesmo `StepWorker`
(thread + sinais) que a interface usa — em dry-run/status, sem instalar nada.

Roda offscreen (QT_QPA_PLATFORM=offscreen). Sai != 0 se qualquer fase falhar,
imprimindo um relatorio. Serve para caçar bugs que os testes stubados nao pegam:
sondas que quebram no sistema real, construcao de widget, o caminho de execucao
com threads/sinais, etc.
"""

from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

from PySide6.QtWidgets import QApplication

# Sem rede: releases do GitHub sao stubados para o driver ser deterministico.
import reforja.steps.appimage as _appimage_mod
import reforja.steps.dev as _dev_mod
from reforja.gui.main_window import BatchPreviewDialog, ItemCard, MainWindow
from reforja.gui.step_runner import BatchProbeWorker, build_gui_step
from reforja.planning import collect_plans
from reforja.steps import ALL_STEPS

for _mod in (_appimage_mod, _dev_mod):
    if hasattr(_mod, "fetch_json"):
        _mod.fetch_json = lambda *a, **k: None  # noqa: E731


class AutoDriverInteraction:
    """Responde os prompts sem UI, no proprio thread do worker (nao trava offscreen).

    Escolhe TODOS os itens (para exercitar o run() de cada um em dry-run) e recusa
    confirmacoes destrutivas (nunca apaga nada de verdade no container)."""

    def ask_text(self, prompt, *, detail=None, prompt_label="Resposta", allow_empty=True) -> str:
        return ""

    def ask_secret(self, prompt, *, detail=None, prompt_label="Senha") -> str:
        return "dry-run"

    def confirm_phrase(self, phrase, *, detail=None) -> bool:
        return False

    def choose_many(self, prompt, options, *, detail=None, preselected=()) -> list[int]:
        return list(range(len(list(options))))


class Report:
    def __init__(self) -> None:
        self.failures: list[tuple[str, str]] = []
        self.notes: list[str] = []

    def ok(self, msg: str) -> None:
        print(f"  \033[32m✓\033[0m {msg}")

    def note(self, msg: str) -> None:
        self.notes.append(msg)
        print(f"  \033[36m·\033[0m {msg}")

    def fail(self, where: str, exc: BaseException) -> None:
        self.failures.append((where, repr(exc)))
        print(f"  \033[31m✗ {where}: {exc!r}\033[0m")
        traceback.print_exc()


def run_gui_worker(app, window, step_cls, action, *, selection=None, force=None, timeout=90):
    """Roda uma acao pela GUI exatamente como a interface: StepWorker (thread) +
    sinais, girando o event loop ate terminar. Retorna (result, failed)."""
    from reforja.gui.step_runner import StepWorker

    worker = StepWorker(
        step_cls,
        action,
        window._logger,
        askpass=None,
        interactive_executor=None,
        run_dir=window._run_dir,
        selection=selection,
        force_keys=force,
    )
    state: dict = {}
    worker.resultReady.connect(lambda r: state.__setitem__("result", r))
    worker.failed.connect(lambda s, m: state.__setitem__("failed", (s, m)))
    worker.finished.connect(lambda: state.__setitem__("done", True))
    worker.start()
    started = time.monotonic()
    while not state.get("done"):
        app.processEvents()
        if time.monotonic() - started > timeout:
            worker.stop()
            state["failed"] = ("timeout", f"excedeu {timeout}s")
            break
        time.sleep(0.005)
    worker.wait(3000)
    return state.get("result"), state.get("failed")


def main() -> int:
    app = QApplication.instance() or QApplication([])
    from reforja.gui import theme

    app.setStyleSheet(theme.build_stylesheet())
    report = Report()
    run_dir = Path("/tmp/reforja-drive")
    run_dir.mkdir(parents=True, exist_ok=True)

    # -- 0. Distro real detectada -------------------------------------------------
    from reforja.platform import detect_distro

    distro = detect_distro()
    print(f"\n== Ambiente ==\n  distro={distro.id} family={distro.family} immutable={distro.immutable}")

    # -- 1. Construcao da janela + navegacao -------------------------------------
    print("\n== 1. Janela + navegacao ==")
    try:
        window = MainWindow(run_dir)
        window._logger.interaction = AutoDriverInteraction()
        window.resize(1200, 820)
        window.show()
        app.processEvents()
        report.ok(f"MainWindow construida ({window._menu.count()} secoes, {window._pages.count()} paginas)")
        for row in range(window._menu.count()):
            window._menu.setCurrentRow(row)
            app.processEvents()
        report.ok("navegou por todas as secoes do menu")
    except Exception as exc:
        report.fail("construcao/navegacao", exc)
        return _finish(report)

    # -- 2. Abre a pagina de cada etapa e monta os ItemCards ----------------------
    print("\n== 2. Paginas de etapa (cards) ==")
    for step_cls in ALL_STEPS:
        try:
            window._open_step_page(step_cls.id)
            page = window._step_pages[step_cls.id]
            page.show()
            app.processEvents()
            if not page._built:
                page._build_cards()
                page._built = True
            app.processEvents()
            n_cards = len(page._cards)
            report.ok(f"etapa {step_cls.id} '{step_cls.title}': {n_cards} card(s)")
        except Exception as exc:
            report.fail(f"pagina etapa {step_cls.id}", exc)

    # -- 3. Sondagem real de cada etapa + aplica nos cards ------------------------
    print("\n== 3. Sondagem real do estado ==")
    for step_cls in ALL_STEPS:
        try:
            step = build_gui_step(
                step_cls, window._logger, dry_run=True, askpass=None, interactive_executor=None, run_dir=run_dir
            )
            tasks = step.plan()
            aplicados = sum(1 for t in tasks if t.state == "aplicado")
            pend = sum(1 for t in tasks if t.state == "pendente")
            desconhecidos = [t.label for t in tasks if t.state == "desconhecido"]
            page = window._step_pages[step_cls.id]
            page._apply_probe(tasks)
            app.processEvents()
            # Um item aplicado deve virar ItemCard sem botao primario de instalar.
            for t in tasks:
                card = page._card_by_key.get(t.key)
                if card is not None and t.state == "aplicado":
                    assert card._action.isHidden(), f"card '{t.label}' aplicado ainda oferece Instalar"
            msg = f"etapa {step_cls.id}: {aplicados} instalado(s), {pend} pendente(s)"
            if desconhecidos:
                msg += f", {len(desconhecidos)} desconhecido(s): {', '.join(desconhecidos[:3])}"
                report.note(msg)
            else:
                report.ok(msg)
        except Exception as exc:
            report.fail(f"sondagem etapa {step_cls.id}", exc)

    # -- 4. Cada ItemCard em cada estado (render) ---------------------------------
    print("\n== 4. Render de ItemCard por estado ==")
    from reforja.steps_base import StepTask

    try:
        for estado in ("pendente", "aplicado", "acao", "indisponivel"):
            t = StepTask(key="k", label="Exemplo", run=lambda: None, category="utilitarios")
            t.state = estado
            if estado == "indisponivel":
                t.available = False
                t.unavailable_reason = "sem touchpad"
            card = ItemCard(ALL_STEPS[0], t, window)
            card.apply_task_state(t)
        report.ok("ItemCard renderiza em pendente/aplicado/acao/indisponivel")
    except Exception as exc:
        report.fail("render ItemCard", exc)

    # -- 5. Previa consolidada (Aplicar tudo) -------------------------------------
    print("\n== 5. Previa multi-coluna (Aplicar tudo) ==")
    try:

        def build(cls):
            return build_gui_step(
                cls, window._logger, dry_run=True, askpass=None, interactive_executor=None, run_dir=run_dir
            )

        plans = collect_plans(list(ALL_STEPS), window._logger, build)
        dialog = BatchPreviewDialog(plans, window, title="Aplicar tudo")
        dialog.show()
        app.processEvents()
        sel, force = dialog.result_selection()
        marcados = sum(len(v) for v in sel.values())
        report.ok(f"previa montada: {len(plans)} etapa(s), {marcados} item(ns) pre-marcado(s) (o que falta)")
        dialog.close()
        # BatchProbeWorker (thread) tambem deve rodar sem quebrar.
        bstate: dict = {}
        bw = BatchProbeWorker(list(ALL_STEPS), window._logger, run_dir=run_dir)
        bw.probed.connect(lambda pl: bstate.__setitem__("plans", pl))
        bw.finished.connect(lambda: bstate.__setitem__("done", True))
        bw.start()
        t0 = time.monotonic()
        while not bstate.get("done") and time.monotonic() - t0 < 60:
            app.processEvents()
            time.sleep(0.005)
        bw.wait(2000)
        report.ok(f"BatchProbeWorker sondou {len(bstate.get('plans', []))} etapa(s) em background")
    except Exception as exc:
        report.fail("previa/batch-probe", exc)

    # -- 6. Executa cada etapa pela GUI: Status (real) + Apply (dry-run) ----------
    print("\n== 6. Execucao pela GUI (StepWorker): status + apply dry-run ==")
    for step_cls in ALL_STEPS:
        for action in ("status", "dry-run"):
            try:
                result, failed = run_gui_worker(app, window, step_cls, action)
                rotulo = "apply(dry-run)" if action == "dry-run" else "status"
                if failed:
                    report.fail(f"etapa {step_cls.id} {rotulo}", RuntimeError(f"{failed[0]}: {failed[1]}"))
                elif result is None:
                    report.fail(f"etapa {step_cls.id} {rotulo}", RuntimeError("sem resultado"))
                else:
                    report.ok(f"etapa {step_cls.id} {rotulo}: status={result.status} compliance={result.compliance}")
            except Exception as exc:
                report.fail(f"etapa {step_cls.id} {action}", exc)

    # -- 7. Undo (dry-run, sem confirmar) pelas etapas que desfazem ---------------
    print("\n== 7. Undo (dry-run, sem confirmacao) ==")
    from reforja.gui.main_window import _has_undo

    for step_cls in ALL_STEPS:
        if not _has_undo(step_cls):
            continue
        try:
            step = build_gui_step(
                step_cls, window._logger, dry_run=True, askpass=None, interactive_executor=None, run_dir=run_dir
            )
            step.undo()
            report.ok(f"etapa {step_cls.id} undo (dry-run) ok")
        except Exception as exc:
            report.fail(f"etapa {step_cls.id} undo", exc)

    # -- 8. Atalho 'Instalar GUI do Reforja' (preselect etapa 15) -----------------
    print("\n== 8. Atalho Instalar GUI do Reforja (etapa 15 preselect) ==")
    try:
        appimage_cls = next(s for s in ALL_STEPS if s.id == "15")
        step = build_gui_step(
            appimage_cls, window._logger, dry_run=True, askpass=None, interactive_executor=None, run_dir=run_dir
        )
        step.preselect_names = ("Reforja",)
        step.apply()
        report.ok("preselect 'Reforja' aplicou em dry-run")
    except Exception as exc:
        report.fail("preselect Reforja", exc)

    # -- 9. Modelo Flathub: instalado NAO reinstala; forcar reinstala -------------
    # Feature principal do retrabalho. Usa a etapa de catalogo (10) com a deteccao
    # forcada: um app "instalado" nao deve rodar; um "pendente" deve; e reinstalar
    # (force_keys) roda o instalado. Tudo em dry-run (o run so registra a chamada).
    print("\n== 9. Modelo Flathub (instalado nao reinstala) ==")
    try:
        apps_cls = next(s for s in ALL_STEPS if s.id == "10")
        instalado, pendente = "Discord", "auto-cpufreq"

        def build_apps():
            step = build_gui_step(
                apps_cls, window._logger, dry_run=True, askpass=None, interactive_executor=None, run_dir=run_dir
            )
            executados: list[str] = []
            step._install_app = lambda name: executados.append(name)  # type: ignore[method-assign]
            step._detect_source_label = lambda name: (  # type: ignore[method-assign]
                "instalado via teste" if name == instalado else False
            )
            return step, executados

        # Sem force: o instalado e pulado, o pendente roda.
        step, executados = build_apps()
        step.selection = (instalado, pendente)
        step.apply()
        assert instalado not in executados, f"{instalado} (instalado) NAO deveria reinstalar: {executados}"
        assert pendente in executados, f"{pendente} (pendente) deveria instalar: {executados}"
        report.ok(f"sem force: pulou '{instalado}' (instalado), rodou '{pendente}' (pendente)")

        # Com force: reinstala o ja instalado.
        step, executados = build_apps()
        step.selection = (instalado,)
        step.force_keys = {instalado}
        step.apply()
        assert instalado in executados, f"Reinstalar deveria rodar '{instalado}': {executados}"
        report.ok(f"com force: reinstalou '{instalado}'")
    except Exception as exc:
        report.fail("modelo Flathub skip/force", exc)

    # -- 10. Evolucoes: busca/filtro, presets, remover, tema ----------------------
    print("\n== 10. Busca/filtro, presets, remover e tema ==")
    try:
        page = window._step_pages["10"]
        window._open_step_page("10")
        page.show()
        app.processEvents()
        if not page._built:
            page._build_cards()
            page._built = True
        assert page._filter_bar is not None, "etapa 10 deveria ter barra de busca/filtro"
        page._on_search("discord")
        assert [c.key for c in page._visible_cards] == ["Discord"], "busca deveria filtrar so o Discord"
        page._on_search("")
        page._on_category("comunicacao")
        assert all(c._task.category == "comunicacao" for c in page._visible_cards), "filtro de categoria falhou"
        page._on_category("")
        report.ok("busca + filtro de categoria funcionam na etapa 10")
    except Exception as exc:
        report.fail("busca/filtro", exc)

    try:
        from reforja.presets import PRESETS, preset_selection

        for nome in PRESETS:
            sel = preset_selection(nome)
            assert sel, f"preset {nome} vazio"
        report.ok(f"{len(PRESETS)} presets definidos: {', '.join(PRESETS)}")
    except Exception as exc:
        report.fail("presets", exc)

    try:
        from reforja.steps_base import StepTask

        t = StepTask(key="k", label="Removivel", run=lambda: None, remove=lambda: None, category="utilitarios")
        t.state = "aplicado"
        card = ItemCard(ALL_STEPS[0], t, window)
        assert not card._remove.isHidden(), "card aplicado com remove deveria mostrar Remover"
        card.set_busy("Instalando...")
        card.set_error("erro de teste")
        assert card._action.text() == "Repetir"
        report.ok("card: Remover + estados Instalando/Erro/Repetir")
    except Exception as exc:
        report.fail("remover/estados do card", exc)

    try:
        from reforja.gui import theme

        assert theme.build_stylesheet(True) != theme.build_stylesheet(False)
        window._toggle_theme()
        window._toggle_theme()
        report.ok("tema claro/escuro alterna sem erro")
    except Exception as exc:
        report.fail("tema", exc)

    window.close()
    return _finish(report)


def _finish(report: Report) -> int:
    print(f"\n{'=' * 60}")
    total_notes = len(report.notes)
    if report.failures:
        print(f"\033[31mFALHAS: {len(report.failures)}\033[0m ({total_notes} nota(s))")
        for where, exc in report.failures:
            print(f"  - {where}: {exc}")
        return 1
    print(f"\033[32mTUDO OK\033[0m ({total_notes} nota(s) informativa(s))")
    for note in report.notes:
        print(f"  · {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
