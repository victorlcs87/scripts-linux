from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .core import Logger, Runner, UserInfo


@dataclass
class StepResult:
    status: str = "done"
    message: str = ""
    manual_events: int = 0
    hints: list[str] = field(default_factory=list)
    compliance: str = "desconhecido"
    summary: str = ""
    applied_items: list[str] = field(default_factory=list)
    missing_items: list[str] = field(default_factory=list)
    attention_items: list[str] = field(default_factory=list)


@dataclass
class StepContext:
    root: Path
    run_dir: Path
    user: UserInfo
    logger: Logger
    runner: Runner


@dataclass(frozen=True)
class StepGroup:
    """Categoria que agrupa etapas para navegacao (CLI/GUI).

    E uma camada de apresentacao: nao altera as classes de Step nem os IDs.
    """

    id: str
    title: str
    children: tuple[type[Step], ...]


class Step:
    id = "00"
    title = "Etapa"

    def __init__(self, ctx: StepContext) -> None:
        self.ctx = ctx
        self.result = StepResult()

    def apply(self) -> None:
        raise NotImplementedError

    def dry_run(self) -> None:
        dry_ctx = StepContext(
            root=self.ctx.root,
            run_dir=self.ctx.run_dir,
            user=self.ctx.user,
            logger=self.ctx.logger,
            runner=Runner(self.ctx.logger, dry_run=True),
        )
        self.__class__(dry_ctx).apply()

    def status(self) -> None:
        self.ctx.logger.write("Status ainda nao implementado para esta etapa.")

    def undo(self) -> None:
        self.ctx.logger.write("Undo nao disponivel para esta etapa.")

    def mark_done(self, message: str = "") -> None:
        self.result.status = "done"
        self.result.message = message

    def mark_skipped(self, message: str) -> None:
        self.result.status = "skipped"
        self.result.message = message

    def mark_manual(self, message: str) -> None:
        self.result.status = "manual"
        self.result.message = message
        self.result.manual_events += 1

    def mark_applied(self, summary: str, *, items: list[str] | None = None) -> None:
        self.result.compliance = "aplicado"
        self.result.summary = summary
        if items:
            self.result.applied_items = items

    def mark_pending(self, summary: str, *, missing: list[str] | None = None) -> None:
        self.result.compliance = "pendente"
        self.result.summary = summary
        if missing:
            self.result.missing_items = missing

    def mark_attention(self, summary: str, *, attention: list[str] | None = None) -> None:
        self.result.compliance = "atencao"
        self.result.summary = summary
        if attention:
            self.result.attention_items = attention

    def add_hint(self, message: str) -> None:
        self.result.hints.append(message)
