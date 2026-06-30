"""Frontend grafico (PySide6) do sisteminha de pos-formatacao.

A GUI reaproveita integralmente o motor (steps, Runner, StepContext): apenas
substitui o Logger por um GuiLogger que emite sinais Qt e fornece canais
graficos de interacao (dialogos), sudo (askpass) e comandos interativos
(terminal embutido).
"""

from .app import main

__all__ = ["main"]
