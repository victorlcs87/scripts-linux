"""Imports e helpers compartilhados entre os modulos de etapa."""

from __future__ import annotations

import grp
from dataclasses import dataclass

from ..core import (
    Color,
    announce,
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


class InputGroupMixin:
    """Gestao do grupo `input` (gestos, Sunshine), compartilhada entre steps.

    Mixin sobre Step: usa self.ctx (user/runner/logger).
    """

    def _user_in_group(self, group_name: str) -> bool:
        try:
            group = grp.getgrnam(group_name)
        except KeyError:
            return False
        return self.ctx.user.name in group.gr_mem or self.ctx.user.gid == group.gr_gid

    def _ensure_input_group(self) -> bool:
        user = self.ctx.user.name
        if self._user_in_group("input"):
            announce(self.ctx.logger, "done", f"usuario {user} ja esta no grupo input")
            return True
        result = self.ctx.runner.run(
            ["gpasswd", "-a", user, "input"],
            sudo=True,
            check=False,
            action=f"Adicionando {user} ao grupo input",
            show_progress=False,
        )
        if result and result.returncode != 0:
            announce(self.ctx.logger, "warning", f"nao consegui adicionar {user} ao grupo input automaticamente.")
            self.ctx.logger.write(f"Execute manualmente: sudo gpasswd -a {user} input")
            return False
        announce(self.ctx.logger, "warning", "faca logout/login ou reinicie para o grupo input valer nesta sessao.")
        return True


@dataclass
class ProbeResult:
    label: str
    status: str
    summary: str
    details: str = ""
