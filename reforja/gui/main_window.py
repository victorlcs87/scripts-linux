"""Janela principal: menu de navegacao + paginas por grupo + console/terminal.

A navegacao segue o modelo "menu + paginas": a lateral e um menu (uma entrada
por grupo, mais Inicio e Atualizacoes) e cada entrada abre uma pagina. Cada
etapa vira um cartao com titulo, descricao, estado e as acoes Aplicar / Status /
Desfazer. O lote continua existindo: Inicio aplica/verifica tudo e cada pagina
de grupo aplica/verifica o grupo inteiro.

O motor e o mesmo do CLI: as acoes rodam via StepWorker -> dispatch, com log ao
vivo no console (GuiLogger) e o terminal embutido para comandos interativos.
"""

from __future__ import annotations

import html
import os
import re
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..cli import render_run_summary, render_status_overview, synthetic_result
from ..core import StepRunResult
from ..steps import ALL_GROUPS, ALL_STEPS
from ..steps_base import Step as _StepBase
from . import theme
from .askpass import resolve_askpass
from .gui_logger import GuiLogger
from .prompts import GuiInteraction
from .step_runner import StepWorker
from .terminal import TerminalExecutor, TerminalWidget
from .updater import CheckWorker, DownloadWorker, UpdateChecker, running_appimage

_BADGE_RE = re.compile(r"^\[(?P<name>[\w-]+)\]")


def _format_line_html(line: str) -> str:
    safe = html.escape(line)
    match = _BADGE_RE.match(line)
    if match:
        name = match.group("name")
        color = theme.BADGE_COLORS.get(name)
        if color:
            badge_html = f'<span style="color:{color};font-weight:bold">[{html.escape(name)}]</span>'
            return badge_html + safe[len(match.group(0)) :]
    if line.startswith("$ "):
        return f'<span style="color:{theme.CONSOLE_CMD_COLOR}">{safe}</span>'
    return safe


def _has_undo(step: type) -> bool:
    # Detecta undo mesmo herdado (mixin/base intermediaria), sem falso positivo
    # do placeholder da classe base.
    return step.undo is not _StepBase.undo


class StepCard(QFrame):
    """Cartao de uma etapa: titulo, descricao, estado e acoes."""

    def __init__(self, step_cls: type, window: MainWindow) -> None:
        super().__init__()
        self.setObjectName("stepCard")
        self._step_cls = step_cls
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(10)
        title = QLabel(step_cls.title)
        title.setObjectName("cardTitle")
        top.addWidget(title)
        top.addStretch(1)
        self._status = QLabel()
        self._status.setObjectName("cardStatus")
        top.addWidget(self._status)
        layout.addLayout(top)

        description = getattr(step_cls, "description", "") or ""
        if description:
            desc = QLabel(description)
            desc.setObjectName("cardDesc")
            desc.setWordWrap(True)
            layout.addWidget(desc)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        btn_apply = QPushButton("Aplicar")
        btn_apply.setObjectName("primary")
        btn_apply.clicked.connect(lambda: window._run_action("apply", [step_cls]))
        btn_status = QPushButton("Status")
        btn_status.clicked.connect(lambda: window._run_action("status", [step_cls]))
        window._register_button(btn_apply)
        window._register_button(btn_status)
        actions.addWidget(btn_apply)
        actions.addWidget(btn_status)
        if _has_undo(step_cls):
            btn_undo = QPushButton("Desfazer")
            btn_undo.setObjectName("destructive")
            btn_undo.clicked.connect(lambda: window._run_action("undo", [step_cls]))
            window._register_button(btn_undo)
            actions.addWidget(btn_undo)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.set_compliance("desconhecido")

    def set_compliance(self, compliance: str) -> None:
        glyph, color = theme.COMPLIANCE.get(compliance, theme.COMPLIANCE["desconhecido"])
        legenda = {
            "aplicado": "aplicado",
            "pendente": "pendente",
            "atencao": "atencao",
            "desconhecido": "nao verificado",
        }.get(compliance, compliance)
        self._status.setText(f"{glyph}  {legenda}")
        self._status.setStyleSheet(f"color: {color};")


def _page_scaffold(title: str, description: str) -> tuple[QWidget, QVBoxLayout]:
    """Cabecalho comum das paginas: titulo + subtitulo + area de conteudo."""
    page = QWidget()
    outer = QVBoxLayout(page)
    outer.setContentsMargins(28, 24, 28, 24)
    outer.setSpacing(6)
    heading = QLabel(title)
    heading.setObjectName("pageTitle")
    outer.addWidget(heading)
    if description:
        sub = QLabel(description)
        sub.setObjectName("pageDesc")
        sub.setWordWrap(True)
        outer.addWidget(sub)
    outer.addSpacing(8)
    return page, outer


class MainWindow(QMainWindow):
    def __init__(self, run_dir: Path) -> None:
        super().__init__()
        self.setWindowTitle("Reforja - Pos-Formatacao")
        self.resize(1080, 760)
        self._run_dir = run_dir

        # Logger unico da sessao (um arquivo de log), reaproveitado por todas as acoes.
        self._logger = GuiLogger(run_dir, "gui")
        self._logger.signals.output.connect(self._on_output)
        self._logger.signals.transient.connect(self._on_transient)
        self._logger.signals.clearTransient.connect(self._on_clear_transient)
        self._logger.interaction = GuiInteraction(self)

        self._askpass = resolve_askpass()
        self._worker: StepWorker | None = None
        self._running_step: type | None = None
        self._queue: list[tuple[type, str]] = []
        self._queue_action = ""
        self._queue_total = 0
        self._results: list[StepRunResult] = []
        self._transient_active = False

        self._cards: dict[str, StepCard] = {}
        self._action_buttons: list[QPushButton] = []

        self._build_ui()
        self._terminal_executor = TerminalExecutor(self._terminal, on_activate=self._show_terminal)
        self._append("[info] Reforja pronto. Escolha uma secao no menu ao lado.")

        self._updating = False
        self._check_worker: CheckWorker | None = None
        self._download_worker: DownloadWorker | None = None
        self._update_checker: UpdateChecker | None = None

        # Checagem de atualizacao em background (silenciosa em caso de falha).
        # Desabilitavel via env var (testes/headless) para nao deixar uma thread
        # de rede viva durante o teardown do processo.
        if os.environ.get("REFORJA_NO_UPDATE_CHECK") != "1":
            self._update_checker = UpdateChecker()
            self._update_checker.updateAvailable.connect(self._on_update_available)
            self._update_checker.start()

    def _register_button(self, button: QPushButton) -> None:
        """Botoes de acao: desabilitados enquanto algo roda."""
        self._action_buttons.append(button)

    # --- construcao da UI --------------------------------------------------------
    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Vertical)

        # Metade de cima: menu de navegacao + paginas.
        top = QWidget()
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(0)

        sidebar = QWidget()
        sidebar.setObjectName("navSidebar")
        sidebar.setFixedWidth(232)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(10, 14, 10, 12)
        side_layout.setSpacing(2)
        brand = QLabel("⬢ Reforja")
        brand.setObjectName("brandMark")
        side_layout.addWidget(brand)
        brand_sub = QLabel("Pos-formatacao")
        brand_sub.setObjectName("brandSub")
        side_layout.addWidget(brand_sub)

        self._menu = QListWidget()
        self._menu.setObjectName("navMenu")
        self._pages = QStackedWidget()

        # Inicio: acoes em lote sobre tudo.
        self._menu.addItem(QListWidgetItem("Inicio"))
        self._pages.addWidget(self._build_home_page())
        # Uma pagina por grupo.
        for group in ALL_GROUPS:
            self._menu.addItem(QListWidgetItem(group.title))
            self._pages.addWidget(self._build_group_page(group))
        # Atualizacoes.
        self._menu.addItem(QListWidgetItem("Atualizacoes"))
        self._pages.addWidget(self._build_updates_page())

        self._menu.currentRowChanged.connect(self._pages.setCurrentIndex)
        self._menu.setCurrentRow(0)
        side_layout.addWidget(self._menu, 1)

        top_layout.addWidget(sidebar)
        top_layout.addWidget(self._pages, 1)

        # Metade de baixo: console/terminal + progresso + status.
        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(28, 8, 28, 12)
        bottom_layout.setSpacing(8)

        toolbar = QHBoxLayout()
        self._btn_console = QPushButton("Console")
        self._btn_terminal = QPushButton("Terminal")
        self._btn_console.clicked.connect(lambda: self._stack.setCurrentWidget(self._console))
        self._btn_terminal.clicked.connect(self._show_terminal)
        toolbar.addWidget(self._btn_console)
        toolbar.addWidget(self._btn_terminal)
        toolbar.addStretch(1)
        self._btn_stop = QPushButton("Parar")
        self._btn_stop.setObjectName("destructive")
        self._btn_stop.setEnabled(False)
        self._btn_stop.clicked.connect(self._stop_requested)
        toolbar.addWidget(self._btn_stop)
        self._progress = QProgressBar()
        self._progress.setObjectName("progress")
        self._progress.setTextVisible(True)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        toolbar.addWidget(self._progress, 2)
        bottom_layout.addLayout(toolbar)

        self._stack = QStackedWidget()
        self._console = QPlainTextEdit()
        self._console.setObjectName("console")
        self._console.setReadOnly(True)
        self._console.setMaximumBlockCount(8000)
        self._terminal = TerminalWidget()
        self._terminal.setObjectName("terminal")
        self._stack.addWidget(self._console)
        self._stack.addWidget(self._terminal)
        bottom_layout.addWidget(self._stack, 1)

        self._status_label = QLabel(f"Log: {self._logger.path}")
        self._status_label.setObjectName("statusLine")
        bottom_layout.addWidget(self._status_label)

        splitter.addWidget(top)
        splitter.addWidget(bottom)
        splitter.setSizes([470, 290])
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        self.setCentralWidget(splitter)

    def _build_home_page(self) -> QWidget:
        page, layout = _page_scaffold(
            "Inicio",
            "Aplica ou verifica todas as etapas de uma vez. Para agir em uma etapa "
            "especifica, escolha a secao dela no menu.",
        )
        row = QHBoxLayout()
        row.setSpacing(10)
        btn_apply = QPushButton("Aplicar tudo")
        btn_apply.setObjectName("primary")
        btn_apply.clicked.connect(lambda: self._run_action("apply", list(ALL_STEPS)))
        btn_status = QPushButton("Status geral")
        btn_status.clicked.connect(lambda: self._run_action("status", list(ALL_STEPS)))
        self._register_button(btn_apply)
        self._register_button(btn_status)
        row.addWidget(btn_apply)
        row.addWidget(btn_status)
        row.addStretch(1)
        layout.addLayout(row)

        hint = QLabel(
            "Aplicar deixa voce marcar quais itens de cada etapa executar (o que ja "
            "existe na maquina vem marcado). Status apenas verifica o estado, sem mudar nada."
        )
        hint.setObjectName("pageDesc")
        hint.setWordWrap(True)
        layout.addSpacing(6)
        layout.addWidget(hint)
        layout.addStretch(1)
        return page

    def _build_group_page(self, group) -> QWidget:
        page, layout = _page_scaffold(group.title, getattr(group, "description", "") or "")
        row = QHBoxLayout()
        row.setSpacing(10)
        children = list(group.children)
        btn_apply = QPushButton("Aplicar grupo")
        btn_apply.setObjectName("primary")
        btn_apply.clicked.connect(lambda: self._run_action("apply", children))
        btn_status = QPushButton("Status do grupo")
        btn_status.clicked.connect(lambda: self._run_action("status", children))
        self._register_button(btn_apply)
        self._register_button(btn_status)
        row.addWidget(btn_apply)
        row.addWidget(btn_status)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addSpacing(6)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 6, 0)
        holder_layout.setSpacing(12)
        for step in children:
            card = StepCard(step, self)
            self._cards[step.id] = card
            holder_layout.addWidget(card)
        holder_layout.addStretch(1)
        scroll.setWidget(holder)
        layout.addWidget(scroll, 1)
        return page

    def _build_updates_page(self) -> QWidget:
        from ._version import __version__

        page, layout = _page_scaffold(
            "Atualizacoes",
            "Mantem o proprio Reforja em dia. A atualizacao automatica so funciona no "
            "executavel (AppImage); rodando do fonte, abre a pagina de download.",
        )
        versao = QLabel(f"Versao instalada: v{__version__}")
        versao.setObjectName("statusLine")
        layout.addWidget(versao)
        layout.addSpacing(8)
        self._btn_update = QPushButton("Verificar atualizacoes")
        self._btn_update.setObjectName("primary")
        self._btn_update.clicked.connect(self._check_updates_manual)
        self._register_button(self._btn_update)
        row = QHBoxLayout()
        row.addWidget(self._btn_update)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)
        return page

    # --- atualizacao do app ------------------------------------------------------
    def _on_update_available(self, tag: str, url: str, sha256_url: str = "") -> None:
        self._append(f"[info] Nova versao disponivel: {tag}")
        self._offer_update(tag, url, sha256_url)

    def _check_updates_manual(self) -> None:
        if self._updating or self._check_worker is not None:
            return
        self._append("[info] Verificando atualizacoes...")
        self._btn_update.setEnabled(False)
        worker = CheckWorker()
        worker.resultReady.connect(self._on_check_result)
        worker.finished.connect(lambda: setattr(self, "_check_worker", None))
        self._check_worker = worker
        worker.start()

    def _on_check_result(self, status: str, tag: str, url: str, sha256_url: str = "") -> None:
        self._btn_update.setEnabled(True)
        if status == "current":
            self._append(f"[done] Voce ja esta na versao mais recente (v{tag}).")
            QMessageBox.information(self, "Atualizacao", f"Voce ja esta na versao mais recente (v{tag}).")
        elif status == "error":
            detail = url or "erro desconhecido"
            self._append(f"[aviso] Nao foi possivel verificar atualizacoes: {detail}")
            QMessageBox.warning(
                self,
                "Atualizacao",
                f"Nao foi possivel verificar atualizacoes.\n\nDetalhe: {detail}",
            )
        else:  # available
            self._offer_update(tag, url, sha256_url)

    def _offer_update(self, tag: str, url: str, sha256_url: str = "") -> None:
        if self._updating:
            return
        answer = QMessageBox.question(
            self,
            "Atualizacao disponivel",
            f"Uma nova versao (v{tag}) esta disponivel. Atualizar agora?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        target = running_appimage()
        if target is None:
            self._append("[aviso] Atualizacao automatica disponivel apenas no AppImage. Abrindo a pagina de download.")
            QMessageBox.information(
                self,
                "Atualizacao",
                "A atualizacao automatica so funciona no executavel (AppImage).\n"
                "Abrindo a pagina de download para baixar manualmente.",
            )
            QDesktopServices.openUrl(QUrl(url))
            return
        self._start_self_update(url, target, tag, sha256_url)

    def _start_self_update(self, url: str, target: Path, tag: str, sha256_url: str = "") -> None:
        self._updating = True
        self._btn_update.setEnabled(False)
        self._set_running(True)
        self._append(f"[info] Baixando e instalando a versao v{tag}...")
        self._progress.setRange(0, 0)  # ocupado ate o primeiro progresso chegar
        worker = DownloadWorker(url, target, sha256_url)
        worker.progress.connect(self._on_download_progress)
        worker.finished.connect(lambda ok, msg, t=tag: self._on_update_finished(ok, msg, t))
        self._download_worker = worker
        worker.start()

    def _on_download_progress(self, percent: int) -> None:
        self._progress.setRange(0, 100)
        self._progress.setValue(percent)

    def _on_update_finished(self, ok: bool, message: str, tag: str) -> None:
        self._download_worker = None
        self._updating = False
        self._progress.setRange(0, 100)
        self._progress.setValue(100 if ok else 0)
        self._set_running(False)
        self._btn_update.setEnabled(True)
        if ok:
            self._append(f"[done] Atualizado para v{tag}. Reabra o Reforja para concluir.")
            QMessageBox.information(
                self,
                "Atualizacao concluida",
                f"Reforja atualizado para a versao v{tag}.\nFeche e reabra o app para usar a nova versao.",
            )
        else:
            self._append(f"[erro] {message}")
            QMessageBox.critical(
                self,
                "Falha na atualizacao",
                f"{message}\n\nVoce pode baixar manualmente em:\nhttps://github.com/victorlcs87/scripts-linux/releases/latest",
            )

    # --- execucao ----------------------------------------------------------------
    def _set_running(self, running: bool) -> None:
        for btn in self._action_buttons:
            btn.setEnabled(not running)
        self._btn_stop.setEnabled(running)

    def _run_action(self, action: str, steps: list[type]) -> None:
        if self._worker is not None:
            return
        if action == "undo":
            steps = [step for step in steps if _has_undo(step)]
        if not steps:
            return
        if not self._confirm_action(action, steps):
            return
        self._run_steps(action, steps)

    def _confirm_action(self, action: str, steps: list[type]) -> bool:
        """Confirmacao para operacoes de maior impacto: Undo e apply em lote grande."""
        if action == "undo":
            titles = "\n".join(f"- {step.title}" for step in steps)
            answer = QMessageBox.question(
                self,
                "Confirmar Undo",
                f"Desfazer o que estas etapas criaram?\n\n{titles}",
            )
            return answer == QMessageBox.StandardButton.Yes
        if action == "apply" and len(steps) > 3:
            answer = QMessageBox.question(
                self,
                "Confirmar aplicacao em lote",
                f"Aplicar {len(steps)} etapas em sequencia?",
            )
            return answer == QMessageBox.StandardButton.Yes
        return True

    def _run_steps(self, action: str, steps: list[type]) -> None:
        if self._worker is not None or not steps:
            return
        self._console.clear()  # descarta o preview/resumo anterior antes de streamar a execucao
        self._queue = [(step, action) for step in steps]
        self._queue_total = len(self._queue)
        self._queue_action = action
        self._results = []
        if len(steps) == 1:
            self._append(f"==== {action.upper()} -> {steps[0].title} ====")
        else:
            self._append(f"==== {action.upper()} EM LOTE ({self._queue_total} etapas) ====")
        self._progress.setValue(0)
        self._next_in_queue()

    def _next_in_queue(self) -> None:
        if not self._queue:
            self._finish_queue()
            return
        step_cls, action = self._queue.pop(0)
        self._running_step = step_cls
        # Barra em modo "ocupado" enquanto o step corrente roda; a contagem por
        # concluidas volta em _on_result/_finish_queue.
        self._progress.setRange(0, 0)
        completed = self._queue_total - len(self._queue) - 1
        self._progress.setFormat(f"{step_cls.title} ({completed + 1}/{self._queue_total})")
        self._append(f"---- {step_cls.title} ----")
        self._start_worker(step_cls, action)

    def _start_worker(self, step_cls: type, action: str) -> None:
        self._set_running(True)
        worker = StepWorker(
            step_cls,
            action,
            self._logger,
            askpass=self._askpass,
            interactive_executor=self._terminal_executor,
            run_dir=self._run_dir,
        )
        worker.resultReady.connect(self._on_result)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_worker_done)
        self._worker = worker
        worker.start()

    def _on_result(self, result: StepRunResult) -> None:
        self._results.append(result)
        card = self._cards.get(result.step_id)
        if card is not None:
            card.set_compliance(result.compliance)
        self._progress.setRange(0, 100)
        self._progress.setValue(int(len(self._results) / max(1, self._queue_total) * 100))
        self._append(f"[{result.status}] {result.title}: {result.message}")

    def _on_failed(self, status: str, message: str) -> None:
        # Mesmo comportamento do CLI (run_steps): a falha entra no resumo como
        # resultado sintetico e o lote PARA; as restantes ficam como blocked.
        kind = "aviso" if status == "manual" else "erro"
        self._append(f"[{kind}] {message}")
        if self._running_step is not None:
            self._results.append(synthetic_result(self._running_step, status, RuntimeError(message)))
        for step_cls, _action in self._queue:
            self._results.append(synthetic_result(step_cls, "blocked", RuntimeError("etapa anterior falhou")))
        self._queue = []

    def _on_worker_done(self) -> None:
        self._worker = None
        self._running_step = None
        if self._queue:
            self._next_in_queue()
        else:
            self._finish_queue()

    def _stop_requested(self) -> None:
        """Botao Parar: cancela o comando corrente e esvazia a fila."""
        self._append("[aviso] Parando: aguardando o comando atual encerrar...")
        self._btn_stop.setEnabled(False)
        for step_cls, _action in self._queue:
            self._results.append(synthetic_result(step_cls, "blocked", RuntimeError("cancelado pelo usuario")))
        self._queue = []
        worker = self._worker
        if worker is not None:
            worker.stop()
        self._terminal.interrupt()

    def _finish_queue(self) -> None:
        self._set_running(False)
        self._progress.setRange(0, 100)
        self._progress.setValue(100)
        self._progress.setFormat("%p%")
        self._render_summary()
        self._stack.setCurrentWidget(self._console)

    def _render_summary(self) -> None:
        # Reusa os paineis ricos do CLI (mesma saida do terminal), escrevendo pelo
        # GuiLogger para que o resumo apareca no console. Vale para 1 ou N etapas.
        if not self._results:
            return
        duration = sum(result.duration_seconds for result in self._results)
        if self._queue_action == "status":
            render_status_overview(self._logger, self._results, self._queue_total, duration)
        else:
            render_run_summary(self._logger, self._queue_action, self._results, self._queue_total, duration)

    # --- saida -------------------------------------------------------------------
    def _append(self, line: str) -> None:
        self._console.appendHtml(_format_line_html(line))

    def _on_output(self, message: str) -> None:
        for line in message.split("\n"):
            self._append(line)

    def _on_transient(self, message: str) -> None:
        self._status_label.setText(message)
        self._transient_active = True

    def _on_clear_transient(self) -> None:
        if self._transient_active:
            self._status_label.setText(f"Log: {self._logger.path}")
            self._transient_active = False

    def _show_terminal(self) -> None:
        self._stack.setCurrentWidget(self._terminal)

    def closeEvent(self, event) -> None:  # noqa: N802 (override Qt)
        if self._worker is not None:
            answer = QMessageBox.question(
                self,
                "Sair",
                "Uma etapa esta em execucao. Deseja realmente sair?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            # Cancela o comando em andamento e espera a thread encerrar.
            self._queue = []
            self._worker.stop()
            self._terminal.interrupt()
            self._worker.wait(3000)
        if self._download_worker is not None:
            self._download_worker.cancel()
        # Aguarda threads de rede terminarem para nao destrui-las em execucao.
        for thread in (self._update_checker, self._check_worker, self._download_worker):
            if thread is not None and thread.isRunning():
                thread.wait(3000)
        event.accept()
