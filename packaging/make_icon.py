#!/usr/bin/env python3
"""Gera o icone do app (assets/sisteminha.png) usando Qt.

Icone simples e reconhecivel: gradiente escuro com um "S" estilizado e um
acento ciano, alinhado a paleta do tema. Rode com QT_QPA_PLATFORM=offscreen.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

SIZE = 512
OUT = Path(__file__).resolve().parent.parent / "assets" / "sisteminha.png"


def render() -> None:
    pix = QPixmap(SIZE, SIZE)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Fundo arredondado com gradiente.
    grad = QLinearGradient(0, 0, SIZE, SIZE)
    grad.setColorAt(0.0, QColor("#1b2030"))
    grad.setColorAt(1.0, QColor("#0c0e13"))
    painter.setBrush(QBrush(grad))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(QRectF(24, 24, SIZE - 48, SIZE - 48), 96, 96)

    # Acento (barra ciano).
    painter.setBrush(QColor("#2b6cff"))
    painter.drawRoundedRect(QRectF(96, 360, SIZE - 192, 40), 20, 20)

    # "S" central.
    font = QFont("DejaVu Sans", 280)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor("#5fe1ff"))
    painter.drawText(QRectF(0, 20, SIZE, SIZE - 120), Qt.AlignmentFlag.AlignCenter, "S")

    painter.end()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(OUT), "PNG")
    print(f"icone gerado: {OUT}")


def main() -> int:
    QApplication(sys.argv)
    render()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
