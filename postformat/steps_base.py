from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .core import Logger, Runner, UserInfo


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
