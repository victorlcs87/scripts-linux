"""Janela principal: sidebar de steps + painel de acao + console/terminal."""

from __future__ import annotations

import html
import re
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core import StepRunResult
from ..steps import ALL_STEPS
from .askpass import resolve_askpass
from .gui_logger import GuiLogger
from .prompts import GuiInteraction
from .step_runner import StepWorker
from .terminal import TerminalExecutor, TerminalWidget
from .updater import UpdateChecker

# Aparencia dos estados de conformidade (compliance) na sidebar.
_COMPLIANCE = {
    "aplicado": ("✓", "#2ee06a"),  # check verde
    "pendente": ("●", "#f2c14e"),  # bolinha amarela
    "atencao": ("⚠", "#ff7043"),  # alerta laranja
    "desconhecido": ("○", "#8a8f98"),  # circulo cinza
}

_BADGE_COLORS = {
    "done": "#2ee06a",
    "summary": "#ff8fd8",
    "info": "#5fd7ff",
    "action": "#5fd7ff",
    "waiting": "#ffd24a",
    "warning": "#ffd24a",
    "skipped": "#ffd24a",
    "manual": "#ffd24a",
    "aviso": "#ffd24a",
    "dry-run": "#ffb454",
    "rodando": "#5fd7ff",
    "choice": "#7dff7d",
    "failed": "#ff5f5f",
    "blocked": "#ff5f5f",
    "erro": "#ff5f5f",
}

_BADGE_RE = re.compile(r"^\[(?P<name>[\w-]+)\]")


def _format_line_html(line: str) -> str:
    safe = html.escape(line)
    match = _BADGE_RE.match(line)
    if match:
        name = match.group("name")
        color = _BADGE_COLORS.get(name)
        if color:
            badge_html = f'<span style="color:{color};font-weight:bold">[{html.escape(name)}]</span>'
            return badge_html + safe[len(match.group(0)) :]
    if line.startswith("$ "):
        return f'<span style="color:#5fe1ff">{safe}</span>'
    return safe


class MainWindow(QMainWindow):
    def __init__(self, run_dir: Path) -> None:
        super().__init__()
        self.setWindowTitle("Sisteminha - Pos-Formatacao")
        self.resize(1080, 720)
        self._run_dir = run_dir

        # Logger unico da sessao (um arquivo de log), reaproveitado por todas as acoes.
        self._logger = GuiLogger(run_dir, "gui")
        self._logger.signals.output.connect(self._on_output)
        self._logger.signals.transient.connect(self._on_transient)
        self._logger.signals.clearTransient.connect(self._on_clear_transient)
        self._logger.interaction = GuiInteraction(self)

        self._askpass = resolve_askpass()
        self._worker: StepWorker | None = None
        self._queue: list[tuple[type, str]] = []
        self._queue_action = ""
        self._queue_total = 0
        self._results: list[StepRunResult] = []
        self._transient_active = False

        self._build_ui()
        self._terminal_executor = TerminalExecutor(self._terminal, on_activate=self._show_terminal)
        self._select_step(0)

        # Checagem de atualizacao em background (silenciosa em caso de falha).
        self._update_checker = UpdateChecker()
        self._update_checker.updateAvailable.connect(self._on_update_available)
        self._update_checker.start()

    def _on_update_available(self, tag: str, url: str) -> None:
        self._append(f"[info] Nova versao disponivel: {tag}")
        answer = QMessageBox.question(
            self,
            "Atualizacao disponivel",
            f"Uma nova versao ({tag}) esta disponivel. Abrir a pagina de download?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(QUrl(url))

    # --- construcao da UI --------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Sidebar
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(12, 16, 12, 16)
        title = QLabel("Etapas")
        title.setObjectName("sidebarTitle")
        side_layout.addWidget(title)
        self._list = QListWidget()
        self._list.setObjectName("stepList")
        for _step in ALL_STEPS:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, "desconhecido")
            self._list.addItem(item)
        self._list.currentRowChanged.connect(self._select_step)
        side_layout.addWidget(self._list, 1)

        # Acoes globais
        self._btn_apply_all = QPushButton("Aplicar tudo")
        self._btn_dry_all = QPushButton("Dry-run tudo")
        self._btn_status_all = QPushButton("Status geral")
        self._btn_apply_all.clicked.connect(lambda: self._run_all("apply"))
        self._btn_dry_all.clicked.connect(lambda: self._run_all("dry-run"))
        self._btn_status_all.clicked.connect(lambda: self._run_all("status"))
        for btn in (self._btn_status_all, self._btn_dry_all, self._btn_apply_all):
            side_layout.addWidget(btn)
        sidebar.setFixedWidth(280)
        root.addWidget(sidebar)

        # Painel principal
        main = QWidget()
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(20, 18, 20, 18)
        main_layout.setSpacing(12)

        self._step_title = QLabel("")
        self._step_title.setObjectName("stepTitle")
        main_layout.addWidget(self._step_title)

        actions = QHBoxLayout()
        self._btn_apply = QPushButton("Aplicar")
        self._btn_apply.setObjectName("primary")
        self._btn_dry = QPushButton("Dry-run")
        self._btn_status = QPushButton("Status")
        self._btn_undo = QPushButton("Undo")
        self._btn_apply.clicked.connect(lambda: self._run_single("apply"))
        self._btn_dry.clicked.connect(lambda: self._run_single("dry-run"))
        self._btn_status.clicked.connect(lambda: self._run_single("status"))
        self._btn_undo.clicked.connect(lambda: self._run_single("undo"))
        for btn in (self._btn_apply, self._btn_dry, self._btn_status, self._btn_undo):
            actions.addWidget(btn)
        actions.addStretch(1)
        main_layout.addLayout(actions)

        # Console + terminal empilhados
        self._stack = QStackedWidget()
        self._console = QPlainTextEdit()
        self._console.setObjectName("console")
        self._console.setReadOnly(True)
        self._console.setMaximumBlockCount(8000)
        self._terminal = TerminalWidget()
        self._terminal.finished.connect(lambda _rc: None)
        self._stack.addWidget(self._console)
        self._stack.addWidget(self._terminal)
        main_layout.addWidget(self._stack, 1)

        bottom = QHBoxLayout()
        self._btn_console = QPushButton("Console")
        self._btn_terminal = QPushButton("Terminal")
        self._btn_console.clicked.connect(lambda: self._stack.setCurrentWidget(self._console))
        self._btn_terminal.clicked.connect(self._show_terminal)
        bottom.addWidget(self._btn_console)
        bottom.addWidget(self._btn_terminal)
        bottom.addStretch(1)
        self._progress = QProgressBar()
        self._progress.setObjectName("progress")
        self._progress.setTextVisible(True)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        bottom.addWidget(self._progress, 2)
        main_layout.addLayout(bottom)

        self._status_label = QLabel(f"Log: {self._logger.path}")
        self._status_label.setObjectName("statusLine")
        main_layout.addWidget(self._status_label)

        root.addWidget(main, 1)
        self.setCentralWidget(central)
        self._refresh_step_items()

    # --- estado dos steps --------------------------------------------------------
    def _refresh_step_items(self) -> None:
        for row, step in enumerate(ALL_STEPS):
            item = self._list.item(row)
            compliance = item.data(Qt.ItemDataRole.UserRole) or "desconhecido"
            glyph, color = _COMPLIANCE.get(compliance, _COMPLIANCE["desconhecido"])
            item.setText(f"{glyph}  {step.id}. {step.title}")
            item.setForeground(QColor(color))

    def _select_step(self, row: int) -> None:
        if row < 0 or row >= len(ALL_STEPS):
            return
        step = ALL_STEPS[row]
        self._step_title.setText(f"{step.id}. {step.title}")
        # Undo so e oferecido quando o step de fato implementa o metodo.
        self._btn_undo.setEnabled("undo" in step.__dict__)

    def _current_step(self) -> type | None:
        row = self._list.currentRow()
        if row < 0:
            return None
        return ALL_STEPS[row]

    # --- execucao ----------------------------------------------------------------
    def _set_running(self, running: bool) -> None:
        for btn in (
            self._btn_apply,
            self._btn_dry,
            self._btn_status,
            self._btn_undo,
            self._btn_apply_all,
            self._btn_dry_all,
            self._btn_status_all,
        ):
            btn.setEnabled(not running)
        if not running:
            self._select_step(self._list.currentRow())

    def _run_single(self, action: str) -> None:
        if self._worker is not None:
            return
        step_cls = self._current_step()
        if step_cls is None:
            return
        self._queue = []
        self._queue_total = 1
        self._results = []
        self._queue_action = action
        self._append(f"==== {action.upper()} -> {step_cls.id}. {step_cls.title} ====")
        self._progress.setValue(0)
        self._start_worker(step_cls, action)

    def _run_all(self, action: str) -> None:
        if self._worker is not None:
            return
        self._queue = [(step, action) for step in ALL_STEPS]
        self._queue_total = len(self._queue)
        self._queue_action = action
        self._results = []
        self._append(f"==== {action.upper()} TODAS AS ETAPAS ({self._queue_total}) ====")
        self._progress.setValue(0)
        self._next_in_queue()

    def _next_in_queue(self) -> None:
        if not self._queue:
            self._finish_queue()
            return
        step_cls, action = self._queue.pop(0)
        done = self._queue_total - len(self._queue) - 1
        self._progress.setValue(int(done / self._queue_total * 100))
        self._append(f"---- {step_cls.id}. {step_cls.title} ----")
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
        for row, step in enumerate(ALL_STEPS):
            if step.id == result.step_id:
                self._list.item(row).setData(Qt.ItemDataRole.UserRole, result.compliance)
                break
        self._refresh_step_items()
        self._append(f"[{result.status}] {result.title}: {result.message}")

    def _on_failed(self, kind: str, message: str) -> None:
        self._append(f"[{kind}] {message}")

    def _on_worker_done(self) -> None:
        self._worker = None
        if self._queue:
            self._next_in_queue()
        else:
            self._finish_queue()

    def _finish_queue(self) -> None:
        self._set_running(False)
        self._progress.setValue(100)
        if self._queue_total > 1:
            self._render_summary()
        self._stack.setCurrentWidget(self._console)

    def _render_summary(self) -> None:
        counts: dict[str, int] = {}
        for result in self._results:
            counts[result.status] = counts.get(result.status, 0) + 1
        resumo = "  ".join(f"{status}: {count}" for status, count in sorted(counts.items()))
        self._append(f"[summary] {self._queue_action} concluido. {resumo}")

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
        event.accept()
