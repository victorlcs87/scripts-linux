"""Execucao de acoes de step numa thread de trabalho.

Monta o StepContext reaproveitando exatamente a mesma logica do CLI, apenas
injetando o GuiLogger, o askpass e o executor de terminal embutido no Runner.
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ..cli import ROOT, default_step_message, default_step_summary
from ..core import (
    CommandInterruptedError,
    InteractiveExecutor,
    PrivilegeEscalationBlockedError,
    PromptInterruptedError,
    Runner,
    StepRunResult,
    detect_user,
)
from ..steps_base import Step, StepContext


def build_gui_step(
    step_cls: type[Step],
    logger,
    *,
    dry_run: bool,
    askpass: str | None,
    interactive_executor: InteractiveExecutor | None,
    run_dir: Path,
) -> Step:
    runner = Runner(logger, dry_run=dry_run)
    runner.askpass = askpass
    runner.interactive_executor = interactive_executor
    ctx = StepContext(
        root=ROOT,
        run_dir=run_dir,
        user=detect_user(),
        logger=logger,
        runner=runner,
    )
    return step_cls(ctx)


class StepWorker(QThread):
    resultReady = Signal(object)  # StepRunResult
    failed = Signal(str, str)  # (tipo, mensagem)

    def __init__(
        self,
        step_cls: type[Step],
        action: str,
        logger,
        *,
        askpass: str | None,
        interactive_executor: InteractiveExecutor | None,
        run_dir: Path,
    ) -> None:
        super().__init__()
        self._step_cls = step_cls
        self._action = action
        self._logger = logger
        self._askpass = askpass
        self._interactive_executor = interactive_executor
        self._run_dir = run_dir

    def run(self) -> None:  # noqa: D401 (override QThread.run)
        try:
            result = self._run_action()
            self.resultReady.emit(result)
        except PrivilegeEscalationBlockedError as exc:
            self.failed.emit("erro", str(exc))
        except CommandInterruptedError as exc:
            self.failed.emit("erro", str(exc))
        except PromptInterruptedError as exc:
            self.failed.emit("aviso", str(exc))
        except Exception as exc:  # noqa: BLE001 - reportar qualquer falha na UI
            self.failed.emit("erro", f"etapa falhou: {exc}")

    def _run_action(self) -> StepRunResult:
        dry = self._action == "dry-run"
        step = build_gui_step(
            self._step_cls,
            self._logger,
            dry_run=dry,
            askpass=self._askpass,
            interactive_executor=self._interactive_executor,
            run_dir=self._run_dir,
        )
        started = time.monotonic()
        if self._action in ("apply", "dry-run"):
            step.apply()
        elif self._action == "status":
            step.status()
        elif self._action == "undo":
            step.undo()
        else:
            raise ValueError(f"acao invalida: {self._action}")
        if not step.result.message:
            step.result.message = default_step_message(self._action, step.result.status)
        if not step.result.summary:
            step.result.summary = default_step_summary(self._action, step.result)
        return StepRunResult(
            step_id=self._step_cls.id,
            title=self._step_cls.title,
            status=step.result.status,
            message=step.result.summary,
            compliance=step.result.compliance,
            duration_seconds=time.monotonic() - started,
        )
