"""Imports e helpers compartilhados entre os modulos de etapa."""

from __future__ import annotations

from dataclasses import dataclass

from ..core import (
    Color,
    badge,
    divider,
    paint,
)
from ..steps_base import Step


def header(step: Step, title: str, subtitle: str | None = None) -> None:
    step.ctx.logger.write("")
    step.ctx.logger.write(divider(char="#", tone=Color.TITLE))
    step.ctx.logger.write(f"{badge(step.id, Color.TITLE)} {paint(title, Color.TITLE)}")
    if subtitle:
        step.ctx.logger.write(paint(subtitle, Color.ACCENT))
    step.ctx.logger.write(divider())


@dataclass
class ProbeResult:
    label: str
    status: str
    summary: str
    details: str = ""
