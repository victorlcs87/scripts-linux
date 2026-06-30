"""Janela principal: sidebar de steps + painel de acao + console/terminal."""

from __future__ import annotations

import html
import os
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
from .updater import CheckWorker, DownloadWorker, UpdateChecker, running_appimage

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
        self.setWindowTitle("Reforja - Pos-Formatacao")
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

    # --- atualizacao do app ------------------------------------------------------
    def _on_update_available(self, tag: str, url: str) -> None:
        # Disparado pela checagem automatica do startup; oferece o update in-place.
        self._append(f"[info] Nova versao disponivel: {tag}")
        self._offer_update(tag, url)

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

    def _on_check_result(self, status: str, tag: str, url: str) -> None:
        self._btn_update.setEnabled(True)
        if status == "current":
            self._append(f"[done] Voce ja esta na versao mais recente (v{tag}).")
            QMessageBox.information(self, "Atualizacao", f"Voce ja esta na versao mais recente (v{tag}).")
        elif status == "error":
            self._append("[aviso] Nao foi possivel verificar atualizacoes (sem rede ou release indisponivel).")
            QMessageBox.warning(
                self,
                "Atualizacao",
                "Nao foi possivel verificar atualizacoes.\nVerifique a conexao ou se ha releases publicados.",
            )
        else:  # available
            self._offer_update(tag, url)

    def _offer_update(self, tag: str, url: str) -> None:
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
            # Rodando do fonte (sem AppImage): a auto-atualizacao nao se aplica.
            self._append("[aviso] Atualizacao automatica disponivel apenas no AppImage. Abrindo a pagina de download.")
            QMessageBox.information(
                self,
                "Atualizacao",
                "A atualizacao automatica so funciona no executavel (AppImage).\n"
                "Abrindo a pagina de download para baixar manualmente.",
            )
            QDesktopServices.openUrl(QUrl(url))
            return
        self._start_self_update(url, target, tag)

    def _start_self_update(self, url: str, target: Path, tag: str) -> None:
        self._updating = True
        self._btn_update.setEnabled(False)
        self._set_running(True)
        self._append(f"[info] Baixando e instalando a versao v{tag}...")
        self._progress.setRange(0, 0)  # modo "ocupado"
        worker = DownloadWorker(url, target)
        worker.finished.connect(lambda ok, msg, t=tag: self._on_update_finished(ok, msg, t))
        self._download_worker = worker
        worker.start()

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
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self._list.addItem(item)
        self._list.currentRowChanged.connect(self._select_step)
        side_layout.addWidget(self._list, 1)

        # Marcar/limpar selecao
        select_row = QHBoxLayout()
        self._btn_check_all = QPushButton("Marcar todas")
        self._btn_check_none = QPushButton("Limpar")
        self._btn_check_all.clicked.connect(lambda: self._set_all_checked(True))
        self._btn_check_none.clicked.connect(lambda: self._set_all_checked(False))
        select_row.addWidget(self._btn_check_all)
        select_row.addWidget(self._btn_check_none)
        side_layout.addLayout(select_row)

        # Acoes em lote: rodam nas etapas marcadas (ou em todas se nenhuma marcada).
        caption = QLabel("Lote: etapas marcadas (ou todas se nenhuma)")
        caption.setObjectName("statusLine")
        caption.setWordWrap(True)
        side_layout.addWidget(caption)
        self._btn_apply_all = QPushButton("Aplicar")
        self._btn_dry_all = QPushButton("Dry-run")
        self._btn_status_all = QPushButton("Status")
        self._btn_apply_all.clicked.connect(lambda: self._run_batch("apply"))
        self._btn_dry_all.clicked.connect(lambda: self._run_batch("dry-run"))
        self._btn_status_all.clicked.connect(lambda: self._run_batch("status"))
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
        self._btn_update = QPushButton("Verificar atualizacoes")
        self._btn_update.clicked.connect(self._check_updates_manual)
        bottom.addWidget(self._btn_update)
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

    def _checked_steps(self) -> list[type]:
        return [
            ALL_STEPS[row]
            for row in range(self._list.count())
            if self._list.item(row).checkState() == Qt.CheckState.Checked
        ]

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self._list.count()):
            self._list.item(row).setCheckState(state)

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
            self._btn_check_all,
            self._btn_check_none,
            self._btn_update,
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

    def _run_batch(self, action: str) -> None:
        # Roda nas etapas marcadas; se nenhuma estiver marcada, roda em todas.
        steps = self._checked_steps() or list(ALL_STEPS)
        self._run_steps(action, steps)

    def _run_steps(self, action: str, steps: list[type]) -> None:
        if self._worker is not None or not steps:
            return
        self._queue = [(step, action) for step in steps]
        self._queue_total = len(self._queue)
        self._queue_action = action
        self._results = []
        self._append(f"==== {action.upper()} EM LOTE ({self._queue_total} etapas) ====")
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
        # Aguarda threads de rede terminarem para nao destrui-las em execucao.
        for thread in (self._update_checker, self._check_worker, self._download_worker):
            if thread is not None and thread.isRunning():
                thread.wait(3000)
        event.accept()
