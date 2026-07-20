"""Janela principal: menu de navegacao + paginas por grupo + paginas de etapa.

Navegacao em tres camadas: a lateral e um menu (Inicio, uma entrada por grupo e
Atualizacoes); cada grupo lista suas etapas em cartoes-resumo; abrir uma etapa
leva a uma pagina estilo Flathub — uma grade multi-coluna de cards, um por item,
com estado (Instalado / Instalar) e acao por card. O lote continua: Inicio aplica
tudo e cada grupo aplica o grupo.

O motor e o mesmo do CLI: as acoes rodam via StepWorker -> dispatch, com log ao
vivo no console (GuiLogger) e o terminal embutido para comandos interativos. A
sondagem de estado roda no ProbeWorker, fora da UI thread, sem pedir sudo.
"""

from __future__ import annotations

import html
import os
import re
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..cli import render_run_summary, render_status_overview, synthetic_result
from ..core import StepRunResult
from ..presets import preset_names, preset_selection
from ..steps import ALL_GROUPS, ALL_STEPS
from ..steps_base import Step as _StepBase
from ..steps_base import StepTask
from . import icons, settings, theme
from .askpass import resolve_askpass
from .gui_logger import GuiLogger
from .prompts import GuiInteraction
from .step_runner import BatchProbeWorker, ProbeWorker, StepWorker, build_gui_step
from .terminal import TerminalExecutor, TerminalWidget
from .updater import (
    UPDATED_ENV,
    CheckWorker,
    DownloadWorker,
    UpdateChecker,
    relaunch_appimage,
    running_appimage,
)

_BADGE_RE = re.compile(r"^\[(?P<name>[\w-]+)\]")

# Largura-alvo de um card de item; a grade calcula as colunas a partir dela.
_CARD_MIN_WIDTH = 300


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


def _installed_label(task: StepTask) -> str:
    """Rotulo curto do chip 'Instalado' com origem/versao, a partir de task.detail.

    detail costuma vir "instalado via flatpak (com.x)" ou "instalado: 1.2.3";
    encurtamos para caber no chip sem perder a informacao (o completo vai no tooltip).
    """
    detail = (task.detail or "").strip()
    if not detail:
        return "instalado"
    low = detail.lower()
    for origem in ("flatpak", "appimage", "sistema", "npm", "cli"):
        if origem in low:
            return origem if origem != "sistema" else "pacote"
    # "instalado: <versao>" -> mostra a versao.
    if ":" in detail:
        return detail.split(":", 1)[1].strip()[:24] or "instalado"
    return detail[:24]


def _card_text(task: StepTask) -> str:
    """Texto curto do card: short_description ou a primeira frase da description."""
    if task.short_description:
        return task.short_description
    desc = task.description.strip()
    if not desc:
        return ""
    first = re.split(r"(?<=[.!?])\s", desc, maxsplit=1)[0]
    return first if len(first) <= 90 else first[:87].rstrip() + "..."


class ItemCard(QFrame):
    """Card de um item (StepTask), no estilo Flathub: icone, nome, estado e acao."""

    def __init__(self, step_cls: type, task: StepTask, window: MainWindow) -> None:
        super().__init__()
        self.setObjectName("itemCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # Altura uniforme (estilo Flathub): evita que a descricao com quebra de linha
        # estoure/corte o card e faca a grade pedir barra de rolagem sem necessidade.
        self.setFixedHeight(96)
        self._step_cls = step_cls
        self._window = window
        self.key = task.key
        self._task = task

        outer = QHBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(12)

        self._icon = QLabel()
        self._icon.setFixedSize(48, 48)
        self._icon.setStyleSheet("background: transparent;")
        self._icon.setPixmap(icons.resolve_task_icon(step_cls.id, task, 48))
        outer.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)

        body = QVBoxLayout()
        body.setSpacing(3)
        # Nome e descricao em uma linha, elididos para a largura do card: labels com
        # size policy Ignored na horizontal para NAO ditar a largura minima do card
        # (senao um titulo longo estoura a coluna e vaza para fora da viewport).
        self._name_full = task.label
        self._name = QLabel(self._name_full)
        self._name.setObjectName("itemName")
        self._name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._name.setToolTip(task.label)
        body.addWidget(self._name)
        self._desc_full = _card_text(task)
        self._desc = QLabel(self._desc_full)
        self._desc.setObjectName("itemDesc")
        self._desc.setWordWrap(False)
        self._desc.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._desc.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._desc.setToolTip(task.description or self._desc_full)
        body.addWidget(self._desc)

        controls = QHBoxLayout()
        controls.setSpacing(6)
        self._state = QLabel()
        self._state.setObjectName("itemState")
        controls.addWidget(self._state)
        self._chip = QLabel()
        self._chip.setVisible(False)
        controls.addWidget(self._chip)
        controls.addStretch(1)
        self._action = QPushButton()
        self._action.setVisible(False)
        window._register_button(self._action)
        controls.addWidget(self._action)
        self._secondary = QPushButton()
        self._secondary.setObjectName("ghost")
        self._secondary.setVisible(False)
        window._register_button(self._secondary)
        controls.addWidget(self._secondary)
        self._remove = QPushButton("Remover")
        self._remove.setObjectName("destructive")
        self._remove.setVisible(False)
        window._register_button(self._remove)
        controls.addWidget(self._remove)
        # Controla o disconnect() sem emitir aviso do libpyside quando nada esta ligado.
        self._action_connected = False
        self._secondary_connected = False
        self._remove_connected = False
        # Guarda o estado corrente para restaurar apos um "Instalando..." transitorio.
        self._busy = False
        body.addSpacing(2)
        body.addLayout(controls)

        outer.addLayout(body, 1)
        self.apply_task_state(task)

    def set_busy(self, label: str = "Processando...") -> None:
        """Estado transitorio durante instalar/remover: chip ocupado, botoes off."""
        self._busy = True
        for widget in (self._chip, self._action, self._secondary, self._remove):
            widget.setVisible(False)
        self._state.setText("")
        self._chip.setObjectName("busyChip")
        self._chip.setText(f"⏳ {label}")
        self._chip.setVisible(True)
        self._repolish()

    def set_error(self, message: str) -> None:
        """Marca falha na acao do card, com botao Repetir."""
        self._busy = False
        for widget in (self._chip, self._secondary, self._remove):
            widget.setVisible(False)
        self._chip.setObjectName("errorChip")
        self._chip.setText("⚠ falhou")
        self._chip.setToolTip(message)
        self._chip.setVisible(True)
        self._state.setText("")
        self._disconnect_action()
        self._action.setObjectName("primary")
        self._action.setText("Repetir")
        self._action.setEnabled(True)
        self._action.setVisible(True)
        self._action.clicked.connect(lambda: self._window._install_item(self._step_cls, self.key, force=True))
        self._action_connected = True
        self._sync_a11y()
        self._repolish()

    def set_icon_pixmap(self, pixmap: QPixmap) -> None:
        if not pixmap.isNull():
            self._icon.setPixmap(
                pixmap.scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            )

    def refresh_icon(self) -> None:
        """Re-resolve o icone (ex.: apos troca de tema, para a cor do avatar)."""
        self._icon.setPixmap(icons.resolve_task_icon(self._step_cls.id, self._task, 48))

    def resizeEvent(self, event) -> None:  # noqa: N802 (override Qt)
        super().resizeEvent(event)
        self._elide_labels()

    def _elide_labels(self) -> None:
        # Elide nome e descricao para a largura disponivel (uma linha limpa cada,
        # sem corte bruto; o texto completo fica no tooltip).
        for label, full in ((self._name, self._name_full), (self._desc, self._desc_full)):
            metrics = label.fontMetrics()
            label.setText(metrics.elidedText(full, Qt.TextElideMode.ElideRight, max(40, label.width())))

    def _disconnect_action(self) -> None:
        if self._action_connected:
            self._action.clicked.disconnect()
            self._action_connected = False

    def apply_task_state(self, task: StepTask) -> None:
        """Reconfigura chip/botoes conforme o estado sondado da tarefa."""
        self._task = task
        self._busy = False
        state = task.state
        # Reset dos controles.
        for widget in (self._chip, self._action, self._secondary, self._remove):
            widget.setVisible(False)
        self._action.setEnabled(True)
        self._secondary.setEnabled(True)
        self._remove.setEnabled(True)

        self._disconnect_action()
        if self._secondary_connected:
            self._secondary.clicked.disconnect()
            self._secondary_connected = False
        if self._remove_connected:
            self._remove.clicked.disconnect()
            self._remove_connected = False

        self.setProperty("applied", "true" if state == "aplicado" else "false")
        self._repolish()

        if state == "indisponivel":
            self._state.setText("")
            self._chip.setObjectName("unavailableChip")
            self._chip.setText("indisponivel")
            self._chip.setToolTip(task.unavailable_reason or "nao se aplica a esta maquina")
            self._chip.setVisible(True)
            self._sync_a11y()
            return

        if not task.runnable:
            self._state.setText(task.detail or "")
            self._sync_a11y()
            return

        if state == "aplicado":
            self._state.setText("")
            self._chip.setObjectName("installedChip")
            # Origem/versao detectada (task.detail vem do _detect_*), enxuta.
            self._chip.setText(f"✓ {_installed_label(task)}")
            if task.detail:
                self._chip.setToolTip(task.detail)
            self._chip.setVisible(True)
            # Reinstalar/atualizar e explicito e secundario (modelo Flathub).
            self._secondary.setText("Atualizar" if self._step_cls.id == "15" else "Reinstalar")
            self._secondary.setVisible(True)
            self._secondary.clicked.connect(lambda: self._window._install_item(self._step_cls, self.key, force=True))
            self._secondary_connected = True
            # Remover so aparece quando a tarefa sabe se remover.
            if task.removable:
                self._remove.setVisible(True)
                self._remove.clicked.connect(lambda: self._window._remove_item(self._step_cls, self.key, task.label))
                self._remove_connected = True
            self._sync_a11y()
            return

        if state == "acao":
            self._state.setText(task.detail or "acao sob demanda")
            self._action.setText("Executar")
        else:  # pendente ou desconhecido
            self._state.setText("" if state == "pendente" else "estado desconhecido")
            self._action.setText("Instalar")
        self._action.setObjectName("primary")
        self._action.setVisible(True)
        self._action.clicked.connect(lambda: self._window._install_item(self._step_cls, self.key, force=False))
        self._action_connected = True
        self._sync_a11y()
        self._repolish()

    def _sync_a11y(self) -> None:
        """Da a cada botao um nome acessivel que inclui o item.

        Sem isso um leitor de tela anuncia dezenas de botoes identicos ("Instalar,
        botao") na grade do catalogo: o nome do app esta num QLabel irmao que nao
        tem associacao nenhuma com o botao.
        """
        for button in (self._action, self._secondary, self._remove):
            text = button.text()
            button.setAccessibleName(f"{text} {self._task.label}" if text else self._task.label)
        self._action.setAccessibleDescription(self._task.description or "")
        # O motivo de indisponibilidade e a falha so existiam em tooltip, que leitor
        # de tela nao alcanca.
        if self._task.state == "indisponivel":
            self.setAccessibleDescription(self._task.unavailable_reason or "nao se aplica a esta maquina")
        else:
            self.setAccessibleDescription(self._task.description or "")

    def _repolish(self) -> None:
        for widget in (self, self._action, self._secondary, self._remove, self._chip):
            widget.style().unpolish(widget)
            widget.style().polish(widget)


class StepPage(QWidget):
    """Pagina de uma etapa: cabecalho + grade multi-coluna de ItemCards."""

    def __init__(self, step_cls: type, window: MainWindow, back_row: int, back_label: str) -> None:
        super().__init__()
        self._step_cls = step_cls
        self._window = window
        self._cards: list[ItemCard] = []
        self._card_by_key: dict[str, ItemCard] = {}
        self._visible_cards: list[ItemCard] = []
        self._built = False
        self._columns = 0
        self._search = ""
        self._category = ""  # "" = todas
        self._filter_bar: QWidget | None = None
        self._chip_buttons: list[QToolButton] = []
        self._search_box: QLineEdit | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 18, 28, 18)
        outer.setSpacing(6)

        back = QPushButton(f"‹  {back_label}")
        back.setObjectName("backLink")
        back.setFlat(True)
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.clicked.connect(lambda: window._go_to_menu_row(back_row))
        back.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        outer.addWidget(back, 0, Qt.AlignmentFlag.AlignLeft)

        header = QHBoxLayout()
        title = QLabel(step_cls.title)
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch(1)
        self._overall = QLabel()
        self._overall.setObjectName("cardStatus")
        header.addWidget(self._overall)
        outer.addLayout(header)

        description = getattr(step_cls, "description", "") or ""
        if description:
            desc = QLabel(description)
            desc.setObjectName("pageDesc")
            desc.setWordWrap(True)
            outer.addWidget(desc)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        btn_apply = QPushButton("Instalar o que falta")
        btn_apply.setObjectName("primary")
        btn_apply.clicked.connect(lambda: window._run_action("apply", [step_cls], on_done=self.refresh))
        btn_status = QPushButton("Status")
        btn_status.clicked.connect(lambda: window._run_action("status", [step_cls]))
        btn_refresh = QPushButton("Atualizar estado")
        btn_refresh.setObjectName("ghost")
        btn_refresh.clicked.connect(self.refresh)
        btn_apply.setShortcut("Ctrl+Return")
        btn_apply.setToolTip("Instalar o que falta (Ctrl+Enter)")
        btn_refresh.setShortcut("F5")
        btn_refresh.setToolTip("Reconferir o estado dos itens (F5)")
        window._register_button(btn_apply)
        window._register_button(btn_status)
        window._register_button(btn_refresh)
        actions.addWidget(btn_apply)
        actions.addWidget(btn_status)
        if _has_undo(step_cls):
            btn_undo = QPushButton("Desfazer")
            btn_undo.setObjectName("destructive")
            btn_undo.clicked.connect(lambda: window._run_action("undo", [step_cls], on_done=self.refresh))
            window._register_button(btn_undo)
            actions.addWidget(btn_undo)
        actions.addStretch(1)
        actions.addWidget(btn_refresh)
        outer.addLayout(actions)

        # Barra de busca + filtro por categoria (montada em _build_cards, quando
        # ja se sabe quantos itens/categorias a etapa tem). So aparece se valer a pena.
        self._filter_holder = QVBoxLayout()
        outer.addLayout(self._filter_holder)
        outer.addSpacing(6)

        # Ctrl+F cai na busca do catalogo (quando a etapa tem barra de filtro).
        focus_search = QShortcut(QKeySequence.StandardKey.Find, self)
        focus_search.activated.connect(self._focus_search)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        # Nunca rolar na horizontal: os cards reflowem/encolhem para caber na largura.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._grid_holder = QWidget()
        self._grid = QGridLayout(self._grid_holder)
        self._grid.setContentsMargins(0, 0, 6, 0)
        self._grid.setSpacing(12)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self._grid_holder)
        outer.addWidget(scroll, 1)

    def showEvent(self, event) -> None:  # noqa: N802 (override Qt)
        super().showEvent(event)
        if not self._built:
            self._build_cards()
            self._built = True
            self.refresh()

    def resizeEvent(self, event) -> None:  # noqa: N802 (override Qt)
        super().resizeEvent(event)
        self._reflow()

    def _build_cards(self) -> None:
        tasks = self._window._step_tasks(self._step_cls)
        if not tasks:
            empty = QLabel("Nada desta etapa se aplica a esta maquina.")
            empty.setObjectName("pageDesc")
            self._grid.addWidget(empty, 0, 0)
            return
        for task in tasks:
            card = ItemCard(self._step_cls, task, self._window)
            self._cards.append(card)
            self._card_by_key[task.key] = card
        self._build_filter_bar(tasks)
        self._visible_cards = list(self._cards)
        self._reflow(force=True)
        self._load_icons(tasks)

    def _build_filter_bar(self, tasks: list[StepTask]) -> None:
        """Busca + chips de categoria. So aparece com itens/categorias suficientes."""
        # (montada sob demanda; _focus_search lida com a etapa que nao tem barra)
        categorias = [c for c in dict.fromkeys(t.category for t in tasks if t.category)]
        if len(tasks) <= 6 or len(categorias) < 2:
            return
        bar = QHBoxLayout()
        bar.setSpacing(8)
        search = QLineEdit()
        search.setPlaceholderText("Buscar...")
        search.setClearButtonEnabled(True)
        search.textChanged.connect(self._on_search)
        search.setMaximumWidth(260)
        # Piso de largura: sem ele os chips de categoria espremem o campo ate "Bus...".
        search.setMinimumWidth(140)
        search.setAccessibleName("Buscar itens desta etapa")
        self._search_box = search
        bar.addWidget(search)
        todos = QToolButton()
        todos.setObjectName("filterChip")
        todos.setText("Todos")
        todos.setCheckable(True)
        todos.setChecked(True)
        todos.clicked.connect(lambda: self._on_category(""))
        todos.setAccessibleName("Mostrar todas as categorias")
        bar.addWidget(todos)
        self._chip_buttons = [todos]
        for cat in categorias:
            chip = QToolButton()
            chip.setObjectName("filterChip")
            chip.setText(cat)
            chip.setCheckable(True)
            chip.clicked.connect(lambda _c=False, c=cat: self._on_category(c))
            chip.setAccessibleName(f"Filtrar por categoria {cat}")
            bar.addWidget(chip)
            self._chip_buttons.append(chip)
        bar.addStretch(1)
        wrapper = QWidget()
        wrapper.setLayout(bar)
        self._filter_holder.addWidget(wrapper)
        self._filter_bar = wrapper

    def _focus_search(self) -> None:
        """Ctrl+F: leva o foco para a busca. Etapas curtas nao tem barra de filtro."""
        if self._search_box is not None:
            self._search_box.setFocus(Qt.FocusReason.ShortcutFocusReason)
            self._search_box.selectAll()

    def _on_search(self, text: str) -> None:
        self._search = text.strip().lower()
        self._apply_filter()

    def _on_category(self, category: str) -> None:
        self._category = category
        for chip in self._chip_buttons:
            chip.setChecked((chip.text() == "Todos" and not category) or chip.text() == category)
        self._apply_filter()

    def _matches(self, card: ItemCard) -> bool:
        task = card._task
        if self._category and task.category != self._category:
            return False
        if self._search:
            haystack = f"{task.label} {task.short_description} {task.description}".lower()
            if self._search not in haystack:
                return False
        return True

    def _apply_filter(self) -> None:
        self._visible_cards = [card for card in self._cards if self._matches(card)]
        for card in self._cards:
            card.setVisible(card in self._visible_cards)
        self._reflow(force=True)

    def _reflow(self, *, force: bool = False) -> None:
        cards = self._visible_cards or self._cards
        if not cards:
            return
        width = self._grid_holder.width() or self.width()
        # N colunas cabem quando N*card + (N-1)*spacing <= largura. Resolvendo por N
        # (inclui o espacamento entre colunas, senao a ultima coluna estoura a direita).
        spacing = self._grid.spacing()
        columns = max(1, min(len(cards), (width + spacing) // (_CARD_MIN_WIDTH + spacing)))
        if columns == self._columns and not force:
            return
        self._columns = columns
        while self._grid.count():
            self._grid.takeAt(0)
        for index, card in enumerate(cards):
            self._grid.addWidget(card, index // columns, index % columns)
            card.setVisible(True)
        for col in range(columns):
            self._grid.setColumnStretch(col, 1)

    def _load_icons(self, tasks: list[StepTask]) -> None:
        # Ambiente headless (testes/CI): nao dispara o download de icone do Flathub.
        # Isso evita uma thread de rede pendente no teardown do processo (QThread
        # destruida em execucao -> abort), alem de manter os testes deterministicos.
        if os.environ.get("REFORJA_NO_UPDATE_CHECK") == "1":
            return
        targets = icons.flathub_icon_targets(tasks)
        if not targets:
            return
        worker = icons.FlathubIconWorker(targets)
        worker.iconReady.connect(self._on_icon_ready)
        self._window._track_worker(worker)
        worker.start()

    def _on_icon_ready(self, key: str, path: str) -> None:
        card = self._card_by_key.get(key)
        if card is not None:
            pix = QPixmap(path)
            if not pix.isNull():
                card.set_icon_pixmap(pix)

    def refresh(self) -> None:
        """Re-sonda o estado das tarefas em segundo plano e atualiza os cards."""
        if not self._built or not self._cards:
            return
        self._overall.setText("verificando...")
        self._overall.setStyleSheet(f"color: {theme.PALETTE['text_faint']};")
        self._window._probe_step(self._step_cls, self._apply_probe)

    def _apply_probe(self, tasks: list[StepTask]) -> None:
        applied = pending = 0
        for task in tasks:
            card = self._card_by_key.get(task.key)
            if card is not None:
                card.apply_task_state(task)
            if task.state == "aplicado":
                applied += 1
            elif task.state == "pendente":
                pending += 1
        total = applied + pending
        if total and pending == 0:
            compliance = "aplicado"
        elif applied:
            compliance = "pendente"
        else:
            compliance = "pendente" if pending else "desconhecido"
        glyph, color = theme.compliance(compliance)
        legenda = f"{applied}/{total} instalado(s)" if total else "sem itens"
        self._overall.setText(f"{glyph}  {legenda}")
        self._overall.setStyleSheet(f"color: {color};")


class StepSummaryCard(QFrame):
    """Cartao-resumo de uma etapa na pagina do grupo: leva a pagina da etapa."""

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
        btn_open = QPushButton("Abrir")
        btn_open.setObjectName("primary")
        btn_open.clicked.connect(lambda: window._open_step_page(step_cls.id))
        btn_status = QPushButton("Status")
        btn_status.clicked.connect(lambda: window._run_action("status", [step_cls]))
        window._register_button(btn_open)
        window._register_button(btn_status)
        actions.addWidget(btn_open)
        actions.addWidget(btn_status)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.set_compliance("desconhecido")

    def set_compliance(self, compliance: str) -> None:
        glyph, color = theme.compliance(compliance)
        legenda = {
            "aplicado": "aplicado",
            "pendente": "pendente",
            "atencao": "atencao",
            "desconhecido": "nao verificado",
        }.get(compliance, compliance)
        self._status.setText(f"{glyph}  {legenda}")
        self._status.setStyleSheet(f"color: {color};")


class BatchPreviewDialog(QDialog):
    """Previa consolidada do Aplicar: itens de todas as etapas em colunas.

    Substitui o modal por etapa: uma unica tela, agrupada por etapa, com os itens
    em varias colunas e poucas linhas. Vem marcado o que FALTA; o que ja esta
    instalado aparece esmaecido e desmarcado (marcar = reinstalar). Devolve a
    selecao ({step_id: (keys,)}) e o conjunto a forcar (itens ja instalados que o
    usuario marcou de proposito).
    """

    _COLUMNS = 3

    def __init__(
        self,
        plans,
        parent: MainWindow,
        *,
        title: str,
        initial_selection: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 580)
        self._checks: list[tuple[str, str, str, QCheckBox]] = []
        self._step_ids = [step_cls.id for step_cls, _step, _tasks in plans]
        # Preset: quando presente, pre-marca exatamente esses itens (em vez do
        # padrao "so o que falta"). () numa etapa = todos os itens preselecionaveis.
        self._initial = initial_selection

        outer = QVBoxLayout(self)
        outer.setSpacing(10)
        if initial_selection is not None:
            texto = (
                "Kit do perfil pre-marcado. Revise e ajuste o que quiser antes de instalar. "
                "Itens ja instalados aparecem esmaecidos."
            )
        else:
            texto = (
                "Marque o que instalar. O que ja esta instalado vem desmarcado — marque "
                "apenas se quiser reinstalar. Itens que removem coisas nunca vem marcados."
            )
        intro = QLabel(texto)
        intro.setObjectName("pageDesc")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        holder = QWidget()
        vbox = QVBoxLayout(holder)
        vbox.setContentsMargins(0, 0, 6, 0)
        vbox.setSpacing(12)
        any_item = False
        for step_cls, _step, tasks in plans:
            runnable = [task for task in tasks if task.runnable]
            if not runnable:
                continue
            any_item = True
            faltam = sum(1 for task in runnable if task.state in ("pendente", "acao"))
            section = QLabel(f"{step_cls.title}   ({faltam} a instalar)")
            section.setObjectName("sectionLabel")
            vbox.addWidget(section)
            grid_holder = QWidget()
            grid = QGridLayout(grid_holder)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setSpacing(6)
            preset_keys = None if self._initial is None else self._initial.get(step_cls.id)
            for index, task in enumerate(runnable):
                check = QCheckBox(self._label_for(task))
                if task.description:
                    check.setToolTip(task.description)
                if self._initial is not None:
                    # Preset: marca os itens do kit. () = todos os preselecionaveis da etapa.
                    pre = task.preselectable and (
                        step_cls.id in self._initial and (preset_keys == () or task.key in (preset_keys or ()))
                    )
                else:
                    pre = task.preselectable and task.state in ("pendente", "acao")
                check.setChecked(pre)
                if task.state == "aplicado":
                    check.setObjectName("installedCheck")
                grid.addWidget(check, index // self._COLUMNS, index % self._COLUMNS)
                self._checks.append((step_cls.id, task.key, task.state, check))
            for col in range(self._COLUMNS):
                grid.setColumnStretch(col, 1)
            vbox.addWidget(grid_holder)
        if not any_item:
            vbox.addWidget(QLabel("Nada a aplicar nesta selecao."))
        vbox.addStretch(1)
        scroll.setWidget(holder)
        outer.addWidget(scroll, 1)

        selectors = QHBoxLayout()
        btn_missing = QPushButton("Marcar o que falta")
        btn_missing.setObjectName("ghost")
        btn_missing.clicked.connect(self._check_missing)
        btn_none = QPushButton("Desmarcar tudo")
        btn_none.setObjectName("ghost")
        btn_none.clicked.connect(self._check_none)
        selectors.addWidget(btn_missing)
        selectors.addWidget(btn_none)
        selectors.addStretch(1)
        outer.addLayout(selectors)

        buttons = QDialogButtonBox()
        ok = buttons.addButton("Instalar selecionados", QDialogButtonBox.ButtonRole.AcceptRole)
        ok.setObjectName("primary")
        buttons.addButton("Cancelar", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _label_for(self, task) -> str:
        if task.destructive:
            return f"{task.label}  ·  remove"
        if task.state == "aplicado":
            return f"{task.label}  ·  instalado"
        return task.label

    def _check_missing(self) -> None:
        for _sid, _key, state, check in self._checks:
            check.setChecked(state in ("pendente", "acao"))

    def _check_none(self) -> None:
        for _sid, _key, _state, check in self._checks:
            check.setChecked(False)

    def result_selection(self) -> tuple[dict[str, tuple[str, ...]], dict[str, frozenset[str]]]:
        # Toda etapa presente entra com selecao explicita (mesmo vazia), para que
        # nenhuma caia no checkbox por etapa por falta de selecao injetada.
        selection: dict[str, list[str]] = {step_id: [] for step_id in self._step_ids}
        force: dict[str, set[str]] = {}
        for step_id, key, state, check in self._checks:
            if check.isChecked():
                selection[step_id].append(key)
                if state == "aplicado":  # marcar um ja instalado = reinstalar (forcar)
                    force.setdefault(step_id, set()).add(key)
        sel = {step_id: tuple(keys) for step_id, keys in selection.items()}
        forced = {step_id: frozenset(keys) for step_id, keys in force.items()}
        return sel, forced


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

        # Selecao/force por etapa da execucao corrente (injetados na StepWorker).
        self._selection_map: dict[str, tuple[str, ...]] = {}
        self._force_map: dict[str, frozenset[str]] = {}
        self._on_queue_done: Callable[[], None] | None = None
        # Card alvo de uma acao por item (para marcar erro se a acao falhar).
        self._card_action_target: tuple[str, str] | None = None
        # Sondagem em curso para a previa consolidada do Aplicar (evita reentrada).
        self._preview_worker: BatchProbeWorker | None = None

        self._summary_cards: dict[str, StepSummaryCard] = {}
        self._step_pages: dict[str, StepPage] = {}
        self._step_page_index: dict[str, int] = {}
        self._group_row_of_step: dict[str, int] = {}
        self._row_to_page: list[int] = []
        self._action_buttons: list[QPushButton] = []
        self._suppress_nav = False
        # Workers de sondagem/icone vivos (evita coleta de lixo durante a execucao).
        self._aux_workers: list = []

        self._build_ui()
        self._terminal_executor = TerminalExecutor(self._terminal, on_activate=self._show_terminal)
        self._append("[info] Reforja pronto. Escolha uma secao no menu ao lado.")
        self._announce_update_done()

        self._updating = False
        self._restarting = False
        self._updated_tag = ""
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

    def _track_worker(self, worker) -> None:
        """Mantem referencia a um worker auxiliar e limpa quando ele termina."""
        self._aux_workers.append(worker)
        worker.finished.connect(lambda: self._aux_workers.remove(worker) if worker in self._aux_workers else None)

    # --- helpers de motor (usados pelas paginas de etapa) -----------------------
    def _step_tasks(self, step_cls: type) -> list[StepTask]:
        """Tarefas da etapa SEM sondar (nao dispara comandos/sudo ao abrir a pagina)."""
        try:
            step = build_gui_step(
                step_cls, self._logger, dry_run=True, askpass=None, interactive_executor=None, run_dir=self._run_dir
            )
            return step.tasks()
        except Exception as exc:  # noqa: BLE001 - montar tarefas nunca derruba a UI
            self._append(f"[aviso] nao consegui montar os itens de '{step_cls.title}': {exc}")
            return []

    def _probe_step(self, step_cls: type, callback: Callable[[list[StepTask]], None]) -> None:
        worker = ProbeWorker(step_cls, self._logger, run_dir=self._run_dir)
        worker.probed.connect(lambda _sid, tasks: callback(tasks))
        worker.failed.connect(lambda _sid, msg: self._append(f"[aviso] sondagem de '{step_cls.title}' falhou: {msg}"))
        self._track_worker(worker)
        worker.start()

    def _item_card(self, step_id: str, key: str) -> ItemCard | None:
        page = self._step_pages.get(step_id)
        return page._card_by_key.get(key) if page is not None else None

    def _install_item(self, step_cls: type, key: str, *, force: bool) -> None:
        """Instala/reinstala UM item de uma etapa (acao por card, estilo Flathub)."""
        page = self._step_pages.get(step_cls.id)
        on_done = page.refresh if page is not None else None
        card = self._item_card(step_cls.id, key)
        if card is not None:
            card.set_busy("Instalando...")
        self._card_action_target = (step_cls.id, key)
        self._run_action(
            "apply",
            [step_cls],
            selection={step_cls.id: (key,)},
            force={step_cls.id: frozenset({key})} if force else None,
            on_done=on_done,
        )

    def _remove_item(self, step_cls: type, key: str, label: str) -> None:
        """Remove UM item (botao Remover do card), com confirmacao."""
        answer = QMessageBox.question(
            self,
            "Remover",
            f"Remover '{label}'? Isso desinstala/apaga o item desta maquina.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        page = self._step_pages.get(step_cls.id)
        on_done = page.refresh if page is not None else None
        card = self._item_card(step_cls.id, key)
        if card is not None:
            card.set_busy("Removendo...")
        self._card_action_target = (step_cls.id, key)
        self._run_action(
            "remove",
            [step_cls],
            selection={step_cls.id: (key,)},
            on_done=on_done,
        )

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
        self._row_to_page.append(0)
        # Uma pagina por grupo, e o menu row de cada grupo memorizado por etapa.
        for position, group in enumerate(ALL_GROUPS):
            row = position + 1
            self._menu.addItem(QListWidgetItem(group.title))
            page_index = self._pages.addWidget(self._build_group_page(group, row))
            self._row_to_page.append(page_index)
            for step in group.children:
                self._group_row_of_step[step.id] = row
        # Atualizacoes.
        self._menu.addItem(QListWidgetItem("Atualizacoes"))
        updates_index = self._pages.addWidget(self._build_updates_page())
        self._row_to_page.append(updates_index)

        # Paginas de etapa (fora do menu; abertas via "Abrir"/back link).
        for step in ALL_STEPS:
            back_row = self._group_row_of_step.get(step.id, 0)
            back_label = self._menu.item(back_row).text() if back_row < self._menu.count() else "Inicio"
            page = StepPage(step, self, back_row, back_label)
            self._step_pages[step.id] = page
            self._step_page_index[step.id] = self._pages.addWidget(page)

        self._menu.currentRowChanged.connect(self._on_menu_row)
        self._menu.setCurrentRow(0)
        side_layout.addWidget(self._menu, 1)

        # Rodape do sidebar: alternar tema claro/escuro (persistido).
        self._btn_theme = QPushButton()
        self._btn_theme.setObjectName("ghost")
        self._btn_theme.clicked.connect(self._toggle_theme)
        self._update_theme_button()
        side_layout.addWidget(self._btn_theme)

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
        self._btn_collapse = QPushButton()
        self._btn_collapse.setObjectName("ghost")
        self._btn_collapse.clicked.connect(self._toggle_console)
        toolbar.addWidget(self._btn_console)
        toolbar.addWidget(self._btn_terminal)
        toolbar.addWidget(self._btn_collapse)
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
        # Widgets escondidos quando o console e recolhido (mantem so a toolbar).
        self._console_body = (self._stack,)

        self._status_label = QLabel(f"Log: {self._logger.path}")
        self._status_label.setObjectName("statusLine")
        bottom_layout.addWidget(self._status_label)

        splitter.addWidget(top)
        splitter.addWidget(bottom)
        splitter.setSizes([470, 290])
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        self._splitter = splitter
        self._bottom_panel = bottom
        self.setCentralWidget(splitter)
        self._console_collapsed = False
        self._apply_console_collapsed(settings.load().get("console_collapsed", False))

    # --- navegacao ---------------------------------------------------------------
    def _on_menu_row(self, row: int) -> None:
        if self._suppress_nav or row < 0 or row >= len(self._row_to_page):
            return
        self._pages.setCurrentIndex(self._row_to_page[row])

    def _go_to_menu_row(self, row: int) -> None:
        """Volta para uma entrada do menu (usado pelo back link das paginas de etapa)."""
        if self._menu.currentRow() == row:
            self._pages.setCurrentIndex(self._row_to_page[row])
        else:
            self._menu.setCurrentRow(row)

    def _open_step_page(self, step_id: str) -> None:
        index = self._step_page_index.get(step_id)
        if index is None:
            return
        # Mantem o grupo destacado no menu, sem que isso troque a pagina.
        row = self._group_row_of_step.get(step_id, 0)
        self._suppress_nav = True
        self._menu.setCurrentRow(row)
        self._suppress_nav = False
        self._pages.setCurrentIndex(index)

    def _build_home_page(self) -> QWidget:
        page, layout = _page_scaffold(
            "Inicio",
            "Aplica ou verifica todas as etapas de uma vez. Para gerenciar os itens de "
            "uma etapa, escolha a secao dela no menu e abra a etapa.",
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
            "Aplicar deixa voce marcar o que executar em cada etapa; o que ja esta "
            "instalado vem desmarcado e nao e reinstalado. Status apenas verifica o "
            "estado, sem mudar nada."
        )
        hint.setObjectName("pageDesc")
        hint.setWordWrap(True)
        layout.addSpacing(6)
        layout.addWidget(hint)

        # Perfis: kits de itens que atravessam etapas; abrem a previa pre-marcada.
        layout.addSpacing(14)
        presets_title = QLabel("Perfis prontos")
        presets_title.setObjectName("sectionLabel")
        layout.addWidget(presets_title)
        preset_hint = QLabel(
            "Um clique monta a lista do perfil na tela de revisao — voce confere e ajusta antes de instalar."
        )
        preset_hint.setObjectName("pageDesc")
        preset_hint.setWordWrap(True)
        layout.addWidget(preset_hint)
        preset_row = QHBoxLayout()
        preset_row.setSpacing(10)
        for name in preset_names():
            btn = QPushButton(name)
            btn.setObjectName("preset")
            btn.clicked.connect(lambda _c=False, n=name: self._apply_preset(n))
            self._register_button(btn)
            preset_row.addWidget(btn)
        preset_row.addStretch(1)
        layout.addLayout(preset_row)

        layout.addStretch(1)
        return page

    def _apply_preset(self, name: str) -> None:
        """Abre a previa consolidada pre-marcada com o kit do perfil."""
        selection = preset_selection(name)
        step_ids = set(selection)
        steps = [step for step in ALL_STEPS if step.id in step_ids]
        if not steps:
            return
        self._preview_then_apply(steps, None, initial_selection=selection, title=f"Perfil: {name}")

    def _build_group_page(self, group, back_row: int) -> QWidget:
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
            card = StepSummaryCard(step, self)
            self._summary_cards[step.id] = card
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
        worker = DownloadWorker(url, target, sha256_url, tag)
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
            self._append(f"[done] Atualizado para v{tag}.")
            box = QMessageBox(self)
            box.setWindowTitle("Atualizacao concluida")
            box.setIcon(QMessageBox.Icon.Information)
            box.setText(f"Reforja atualizado para a versao v{tag}.\nReiniciar agora para usar a nova versao?")
            btn_restart = box.addButton("Reiniciar agora", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Depois", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(btn_restart)
            box.exec()
            self._updated_tag = tag
            if box.clickedButton() is btn_restart:
                self._restart_after_update()
        else:
            self._append(f"[erro] {message}")
            QMessageBox.critical(
                self,
                "Falha na atualizacao",
                f"{message}\n\nVoce pode baixar manualmente em:\nhttps://github.com/victorlcs87/scripts-linux/releases/latest",
            )

    def _announce_update_done(self) -> None:
        """Avisa que a atualizacao concluiu quando esta instancia foi aberta pelo reinicio."""
        tag = os.environ.pop(UPDATED_ENV, "")
        if not tag:
            return
        self._append(f"[done] Atualizacao concluida: rodando a versao v{tag}.")
        QTimer.singleShot(
            0,
            lambda: QMessageBox.information(
                self,
                "Atualizacao concluida",
                f"Reforja foi atualizado com sucesso e agora esta rodando a versao v{tag}.",
            ),
        )

    def _restart_after_update(self) -> None:
        """Sobe a nova versao num processo desacoplado e fecha esta."""
        self._append("[info] Reiniciando o Reforja na nova versao...")
        if not relaunch_appimage(self._updated_tag):
            self._append("[aviso] Nao foi possivel reiniciar automaticamente. Feche e reabra o app.")
            QMessageBox.warning(
                self,
                "Reinicio",
                "Nao foi possivel reiniciar automaticamente.\nFeche e reabra o Reforja para usar a nova versao.",
            )
            return
        self._restarting = True
        self.close()

    # --- execucao ----------------------------------------------------------------
    def _set_running(self, running: bool) -> None:
        for btn in self._action_buttons:
            btn.setEnabled(not running)
        self._btn_stop.setEnabled(running)

    def _run_action(
        self,
        action: str,
        steps: list[type],
        *,
        selection: dict[str, tuple[str, ...]] | None = None,
        force: dict[str, frozenset[str]] | None = None,
        on_done: Callable[[], None] | None = None,
    ) -> None:
        if self._worker is not None or self._preview_worker is not None:
            return
        if action == "undo":
            steps = [step for step in steps if _has_undo(step)]
        if not steps:
            return
        # Apply sem selecao explicita (Aplicar tudo/grupo, Instalar o que falta):
        # abre a previa multi-coluna, que ja e a confirmacao do lote.
        if action == "apply" and selection is None:
            self._preview_then_apply(steps, on_done)
            return
        if not self._confirm_action(action, steps):
            return
        self._run_steps(action, steps, selection=selection, force=force, on_done=on_done)

    def _confirm_action(self, action: str, steps: list[type]) -> bool:
        """Confirmacao das operacoes de maior impacto (Undo). Apply usa a previa."""
        if action == "undo":
            titles = "\n".join(f"- {step.title}" for step in steps)
            answer = QMessageBox.question(
                self,
                "Confirmar Undo",
                f"Desfazer o que estas etapas criaram?\n\n{titles}",
            )
            return answer == QMessageBox.StandardButton.Yes
        return True

    # --- previa consolidada do Aplicar ------------------------------------------
    def _preview_then_apply(
        self,
        steps: list[type],
        on_done: Callable[[], None] | None,
        *,
        initial_selection: dict[str, tuple[str, ...]] | None = None,
        title: str | None = None,
    ) -> None:
        """Sonda as etapas em segundo plano e abre a previa multi-coluna."""
        if self._preview_worker is not None:
            return
        self._append("[info] Verificando o que ja esta instalado...")
        for btn in self._action_buttons:
            btn.setEnabled(False)
        worker = BatchProbeWorker(steps, self._logger, run_dir=self._run_dir)
        worker.probed.connect(lambda plans: self._on_batch_probed(plans, steps, on_done, initial_selection, title))
        worker.finished.connect(self._on_preview_worker_done)
        self._preview_worker = worker
        worker.start()

    def _on_preview_worker_done(self) -> None:
        self._preview_worker = None
        if self._worker is None:
            for btn in self._action_buttons:
                btn.setEnabled(True)

    def _on_batch_probed(
        self,
        plans,
        steps: list[type],
        on_done: Callable[[], None] | None,
        initial_selection: dict[str, tuple[str, ...]] | None = None,
        title: str | None = None,
    ) -> None:
        has_runnable = any(any(task.runnable for task in tasks) for _cls, _step, tasks in plans)
        if not plans or not has_runnable:
            self._append("[aviso] Nada a aplicar nesta selecao.")
            return
        if title is None:
            title = "Aplicar tudo" if len(steps) == len(ALL_STEPS) else "Aplicar"
        dialog = BatchPreviewDialog(plans, self, title=title, initial_selection=initial_selection)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._append("[aviso] Aplicacao cancelada.")
            return
        selection, force = dialog.result_selection()
        if not any(selection.values()):
            self._append("[aviso] Nenhum item marcado; nada a executar.")
            return
        # Garante selecao explicita para toda etapa do lote (sem cair no modal por etapa).
        full_selection = {step.id: () for step in steps}
        full_selection.update(selection)
        self._run_steps("apply", steps, selection=full_selection, force=force, on_done=on_done)

    def _run_steps(
        self,
        action: str,
        steps: list[type],
        *,
        selection: dict[str, tuple[str, ...]] | None = None,
        force: dict[str, frozenset[str]] | None = None,
        on_done: Callable[[], None] | None = None,
    ) -> None:
        if self._worker is not None or not steps:
            return
        self._console.clear()  # descarta o preview/resumo anterior antes de streamar a execucao
        self._queue = [(step, action) for step in steps]
        self._queue_total = len(self._queue)
        self._queue_action = action
        self._results = []
        self._selection_map = dict(selection or {})
        self._force_map = dict(force or {})
        self._on_queue_done = on_done
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
            selection=self._selection_map.get(step_cls.id),
            force_keys=self._force_map.get(step_cls.id),
        )
        worker.resultReady.connect(self._on_result)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_worker_done)
        self._worker = worker
        worker.start()

    def _on_result(self, result: StepRunResult) -> None:
        self._results.append(result)
        card = self._summary_cards.get(result.step_id)
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
        # Acao por card que falhou: marca o card alvo com erro + Repetir.
        if self._card_action_target is not None:
            step_id, key = self._card_action_target
            card = self._item_card(step_id, key)
            if card is not None:
                card.set_error(message)
            self._card_action_target = None
            self._on_queue_done = None  # nao re-sondar por cima do estado de erro

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
        callback = self._on_queue_done
        self._on_queue_done = None
        self._card_action_target = None
        self._selection_map = {}
        self._force_map = {}
        if callback is not None:
            callback()

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

    # --- tema e console ----------------------------------------------------------
    def _current_theme_dark(self) -> bool:
        return settings.load().get("theme") == "dark"

    def _update_theme_button(self) -> None:
        dark = self._current_theme_dark()
        self._btn_theme.setText("☀  Tema claro" if dark else "🌙  Tema escuro")

    def _toggle_theme(self) -> None:
        dark = not self._current_theme_dark()
        settings.save({"theme": "dark" if dark else "light"})
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.build_stylesheet(dark))
        self._update_theme_button()
        # Re-renderiza os avatares (cores mudam) e o compliance ja lido da paleta ativa.
        for page in self._step_pages.values():
            for card in page._cards:
                card.refresh_icon()

    def _toggle_console(self) -> None:
        self._apply_console_collapsed(not self._console_collapsed)
        settings.save({"console_collapsed": self._console_collapsed})

    def _apply_console_collapsed(self, collapsed: bool) -> None:
        self._console_collapsed = collapsed
        for widget in self._console_body:
            widget.setVisible(not collapsed)
        self._status_label.setVisible(not collapsed)
        self._btn_collapse.setText("▴ Mostrar console" if collapsed else "▾ Ocultar console")
        if collapsed:
            self._splitter.setSizes([740, 40])
        else:
            self._splitter.setSizes([470, 290])

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
        # Aguarda threads de rede/sondagem terminarem para nao destrui-las em execucao.
        for thread in (
            self._update_checker,
            self._check_worker,
            self._download_worker,
            self._preview_worker,
            *self._aux_workers,
        ):
            if thread is not None and thread.isRunning():
                thread.wait(3000)
        event.accept()
