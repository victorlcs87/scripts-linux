"""Janela principal: sidebar de steps + painel de acao + console/terminal."""

from __future__ import annotations

import html
import os
import re
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont
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

from ..cli import render_run_summary, render_status_overview, synthetic_result
from ..core import StepRunResult
from ..planning import describe_step_plain
from ..steps import ALL_GROUPS
from ..steps_base import Step as _StepBase
from .askpass import resolve_askpass
from .gui_logger import GuiLogger
from .prompts import GuiInteraction
from .step_runner import StepWorker, build_gui_step
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
    "aplicado": "#2ee06a",
    "pendente": "#f2c14e",
    "atencao": "#ff7043",
    "ok": "#2ee06a",
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

# Roles dos itens da sidebar: a etapa (classe Step) ou None para cabecalhos de
# grupo; e a string de compliance para colorir o glyph.
_ROLE_STEP = Qt.ItemDataRole.UserRole
_ROLE_COMPLIANCE = int(Qt.ItemDataRole.UserRole) + 1


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
        self._running_step: type | None = None
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
    def _on_update_available(self, tag: str, url: str, sha256_url: str = "") -> None:
        # Disparado pela checagem automatica do startup; oferece o update in-place.
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
        self._list.setSpacing(1)
        # Itens agrupados por categoria: cabecalho (sem checkbox) + etapas-filhas.
        header_font = QFont()
        header_font.setBold(True)
        header_font.setPointSize(max(8, header_font.pointSize() - 1))
        header_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.0)
        for group in ALL_GROUPS:
            header = QListWidgetItem(group.title.upper())
            header.setData(_ROLE_STEP, None)
            header.setFlags(Qt.ItemFlag.ItemIsEnabled)  # nao selecionavel/checkavel
            header.setForeground(QColor("#6f7788"))
            header.setFont(header_font)
            header.setSizeHint(QSize(0, 30))  # respiro acima de cada categoria
            self._list.addItem(header)
            for step in group.children:
                item = QListWidgetItem()
                item.setData(_ROLE_STEP, step)
                item.setData(_ROLE_COMPLIANCE, "desconhecido")
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
                item.setSizeHint(QSize(0, 32))
                item.setToolTip(getattr(step, "description", ""))
                self._list.addItem(item)
        self._list.currentRowChanged.connect(self._select_step)
        self._list.itemClicked.connect(self._on_item_clicked)
        self._list.itemChanged.connect(lambda _item: self._update_undo_enabled())
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

        caption = QLabel("Acoes agem nas etapas marcadas (ou na destacada). Clique no grupo para marcar a categoria.")
        caption.setObjectName("statusLine")
        caption.setWordWrap(True)
        side_layout.addWidget(caption)
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
        self._btn_status = QPushButton("Status")
        self._btn_undo = QPushButton("Undo")
        self._btn_apply.clicked.connect(lambda: self._run_action("apply"))
        self._btn_status.clicked.connect(lambda: self._run_action("status"))
        self._btn_undo.clicked.connect(lambda: self._run_action("undo"))
        for btn in (self._btn_apply, self._btn_status, self._btn_undo):
            actions.addWidget(btn)
        self._btn_stop = QPushButton("Parar")
        self._btn_stop.setEnabled(False)
        self._btn_stop.clicked.connect(self._stop_requested)
        actions.addWidget(self._btn_stop)
        actions.addStretch(1)
        main_layout.addLayout(actions)

        # Console + terminal empilhados
        self._stack = QStackedWidget()
        self._console = QPlainTextEdit()
        self._console.setObjectName("console")
        self._console.setReadOnly(True)
        self._console.setMaximumBlockCount(8000)
        self._terminal = TerminalWidget()
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
    def _step_items(self):
        """Itera (item, step) das etapas, ignorando cabecalhos de grupo."""
        for row in range(self._list.count()):
            item = self._list.item(row)
            step = item.data(_ROLE_STEP)
            if step is not None:
                yield item, step

    def _item_for_step(self, step_id: str):
        for item, step in self._step_items():
            if step.id == step_id:
                return item
        return None

    def _refresh_step_items(self) -> None:
        for item, step in self._step_items():
            compliance = item.data(_ROLE_COMPLIANCE) or "desconhecido"
            glyph, color = _COMPLIANCE.get(compliance, _COMPLIANCE["desconhecido"])
            item.setText(f"{glyph}  {step.title}")
            item.setForeground(QColor(color))

    def _select_step(self, row: int) -> None:
        if row < 0:
            return
        step = self._list.item(row).data(_ROLE_STEP)
        if step is None:  # cabecalho de grupo
            return
        self._step_title.setText(step.title)
        self._update_undo_enabled()
        # Mostra a descricao da etapa no console (preview), sem perturbar execucao.
        if self._worker is None:
            self._show_step_info(step)

    def _show_step_info(self, step) -> None:
        self._stack.setCurrentWidget(self._console)
        if self._results:
            # Ja houve execucao nesta sessao: preserva o resumo no console e so
            # atualiza o titulo/descricao no painel.
            return
        self._console.clear()
        self._append(f"[info] {step.title}")
        for line in describe_step_plain(step, self._step_tasks(step)):
            self._append(line)
        self._append("")
        acoes = "Aplicar deixa voce marcar quais itens executar (ja vem marcado o que existe hoje na maquina)."
        acoes += " Status apenas verifica o estado."
        if self._has_undo(step):
            acoes += " Undo desfaz o que a etapa criou."
        self._append(acoes)
        self._append("Marque a(s) etapa(s) e clique em Aplicar / Status / Undo.")

    def _step_tasks(self, step_cls: type) -> list:
        """Tarefas declaradas pela etapa, SEM sondar o sistema.

        Sondar aqui (plan()) rodaria comandos — alguns com sudo — so por clicar na
        etapa. O estado real aparece pre-marcado no dialogo do Aplicar.
        """
        try:
            instance = build_gui_step(
                step_cls,
                self._logger,
                dry_run=True,
                askpass=self._askpass,
                interactive_executor=self._terminal_executor,
                run_dir=self._run_dir,
            )
            return list(instance.tasks())
        except Exception:
            return []

    @staticmethod
    def _has_undo(step: type) -> bool:
        # Detecta undo mesmo herdado (mixin/base intermediaria), sem falso
        # positivo do placeholder da classe base.
        return step.undo is not _StepBase.undo

    def _update_undo_enabled(self) -> None:
        targets = self._target_steps()
        self._btn_undo.setEnabled(any(self._has_undo(step) for step in targets))

    def _current_step(self) -> type | None:
        item = self._list.currentItem()
        return item.data(_ROLE_STEP) if item is not None else None

    def _checked_steps(self) -> list[type]:
        return [step for item, step in self._step_items() if item.checkState() == Qt.CheckState.Checked]

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for item, _step in self._step_items():
            item.setCheckState(state)

    def _on_item_clicked(self, item) -> None:
        # Clique num cabecalho de grupo marca/desmarca todas as filhas do grupo.
        if item.data(_ROLE_STEP) is not None:
            return
        group_title = item.text()
        group = next((g for g in ALL_GROUPS if g.title.upper() == group_title), None)
        if group is None:
            return
        child_ids = {child.id for child in group.children}
        members = [it for it, st in self._step_items() if st.id in child_ids]
        any_unchecked = any(it.checkState() != Qt.CheckState.Checked for it in members)
        state = Qt.CheckState.Checked if any_unchecked else Qt.CheckState.Unchecked
        for it in members:
            it.setCheckState(state)

    # --- execucao ----------------------------------------------------------------
    def _set_running(self, running: bool) -> None:
        for btn in (
            self._btn_apply,
            self._btn_status,
            self._btn_undo,
            self._btn_check_all,
            self._btn_check_none,
            self._btn_update,
        ):
            btn.setEnabled(not running)
        self._btn_stop.setEnabled(running)
        if not running:
            self._select_step(self._list.currentRow())

    def _target_steps(self) -> list[type]:
        """Alvo das acoes: as etapas marcadas; se nenhuma, a etapa destacada."""
        checked = self._checked_steps()
        if checked:
            return checked
        current = self._current_step()
        return [current] if current is not None else []

    def _run_action(self, action: str) -> None:
        steps = self._target_steps()
        if action == "undo":
            steps = [step for step in steps if self._has_undo(step)]
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
        item = self._item_for_step(result.step_id)
        if item is not None:
            item.setData(_ROLE_COMPLIANCE, result.compliance)
        self._refresh_step_items()
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
