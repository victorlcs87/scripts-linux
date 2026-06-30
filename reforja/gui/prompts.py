"""Canal de interacao grafico (dialogos).

Implementa InteractionProvider. Os steps rodam numa thread de trabalho; quando
pedem entrada, emitimos um sinal para a thread de UI abrir o dialogo e bloqueamos
a thread de trabalho ate a resposta (via threading.Event).
"""

from __future__ import annotations

import threading
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QInputDialog, QLineEdit, QWidget

from ..core import PromptInterruptedError


class GuiInteraction(QObject):
    _request = Signal(object)

    def __init__(self, window: QWidget) -> None:
        super().__init__()
        self._window = window
        # QueuedConnection garante que o dialogo abra na thread de UI mesmo
        # quando o sinal e emitido a partir da thread de trabalho.
        self._request.connect(self._handle, Qt.ConnectionType.QueuedConnection)

    # --- chamado na thread de UI -------------------------------------------------
    def _handle(self, req: dict[str, Any]) -> None:
        try:
            if req["kind"] == "text":
                self._handle_text(req)
            elif req["kind"] == "confirm":
                self._handle_confirm(req)
        finally:
            req["event"].set()

    def _handle_text(self, req: dict[str, Any]) -> None:
        label = req["prompt"]
        if req.get("detail"):
            label = f"{label}\n\n{req['detail']}"
        text, ok = QInputDialog.getText(
            self._window,
            "Reforja - entrada necessaria",
            label,
            QLineEdit.EchoMode.Normal,
        )
        if not ok:
            req["cancelled"] = True
            req["result"] = ""
            return
        text = text.strip()
        if not text and not req["allow_empty"]:
            # Reabre ate ter valor ou cancelar.
            self._handle_text(req)
            return
        req["result"] = text

    def _handle_confirm(self, req: dict[str, Any]) -> None:
        phrase = req["phrase"]
        label = f"Digite {phrase} para confirmar a operacao."
        if req.get("detail"):
            label = f"{label}\n\n{req['detail']}"
        text, ok = QInputDialog.getText(
            self._window,
            "Reforja - confirmacao",
            label,
            QLineEdit.EchoMode.Normal,
        )
        req["result"] = bool(ok) and text.strip() == phrase

    # --- chamado na thread de trabalho (InteractionProvider) ---------------------
    def ask_text(
        self,
        prompt: str,
        *,
        detail: str | None = None,
        prompt_label: str = "Resposta",
        allow_empty: bool = True,
    ) -> str:
        req: dict[str, Any] = {
            "kind": "text",
            "prompt": prompt,
            "detail": detail,
            "allow_empty": allow_empty,
            "result": "",
            "cancelled": False,
            "event": threading.Event(),
        }
        self._request.emit(req)
        req["event"].wait()
        if req["cancelled"]:
            raise PromptInterruptedError(f"entrada cancelada pelo usuario: {prompt}")
        return req["result"]

    def confirm_phrase(self, phrase: str, *, detail: str | None = None) -> bool:
        req: dict[str, Any] = {
            "kind": "confirm",
            "phrase": phrase,
            "detail": detail,
            "result": False,
            "event": threading.Event(),
        }
        self._request.emit(req)
        req["event"].wait()
        return bool(req["result"])
