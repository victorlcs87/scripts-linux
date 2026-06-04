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


@dataclass
class StepContext:
    root: Path
    run_dir: Path
    user: UserInfo
    logger: Logger
    runner: Runner


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

    def add_hint(self, message: str) -> None:
        self.result.hints.append(message)
