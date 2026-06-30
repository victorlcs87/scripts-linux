"""Logger que emite sinais Qt em vez de escrever no stdout.

Mantem o log em arquivo (heranca de Logger) e redireciona a saida de console
para a UI atraves de sinais. Como os steps rodam numa thread de trabalho, os
sinais cruzam para a thread de UI via QueuedConnection automaticamente.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..core import Logger, strip_ansi


class LoggerSignals(QObject):
    output = Signal(str)
    transient = Signal(str)
    clearTransient = Signal()


class GuiLogger(Logger):
    def __init__(self, run_dir: Path, name: str) -> None:
        super().__init__(run_dir, name)
        # Numa GUI o stdout normalmente nao e um tty; forcamos o uso de transient
        # para que o spinner/progresso apareca no console da interface.
        self._tty = True
        self.signals = LoggerSignals()

    def _emit_console(self, message: str) -> None:
        self.signals.output.emit(strip_ansi(message))

    def transient(self, message: str) -> None:
        self._transient_active = True
        self.signals.transient.emit(strip_ansi(message))

    def clear_transient(self) -> None:
        if not self._transient_active:
            return
        self._transient_active = False
        self.signals.clearTransient.emit()
