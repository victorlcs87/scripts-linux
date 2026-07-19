"""Execucao de acoes de step numa thread de trabalho.

Monta o StepContext reaproveitando exatamente a mesma logica do CLI, apenas
injetando o GuiLogger, o askpass e o executor de terminal embutido no Runner.
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ..cli import ROOT
from ..core import (
    CommandInterruptedError,
    InteractiveExecutor,
    PrivilegeEscalationBlockedError,
    PromptInterruptedError,
    Runner,
    StepRunResult,
    detect_user,
)
from ..dispatch import dispatch_action, finalize_result
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


class ProbeWorker(QThread):
    """Sonda as tarefas de uma etapa em segundo plano (state/detail preenchidos).

    Roda `step.plan()` fora da UI thread para os cards mostrarem 'Instalado' vs
    'Instalar' sem travar a interface. A sondagem so le o sistema (pacman -Q,
    flatpak list, arquivos) — nao instala nada nem deveria pedir sudo.
    """

    probed = Signal(str, object)  # (step_id, list[StepTask]) — objetos Python simples
    failed = Signal(str, str)  # (step_id, mensagem)

    def __init__(self, step_cls: type[Step], logger, *, run_dir: Path) -> None:
        super().__init__()
        self._step_cls = step_cls
        self._logger = logger
        self._run_dir = run_dir

    def run(self) -> None:  # noqa: D401 (override QThread.run)
        try:
            step = build_gui_step(
                self._step_cls,
                self._logger,
                dry_run=True,
                askpass=None,
                interactive_executor=None,
                run_dir=self._run_dir,
            )
            tasks = step.plan()
            self.probed.emit(self._step_cls.id, tasks)
        except Exception as exc:  # noqa: BLE001 - sondar nunca derruba a UI
            self.failed.emit(self._step_cls.id, str(exc))


class BatchProbeWorker(QThread):
    """Sonda VARIAS etapas em segundo plano para a previa consolidada do Aplicar.

    Reaproveita `planning.collect_plans` (mesma sondagem do CLI): devolve, por
    etapa que sonda com sucesso, a lista de tarefas com state/detail preenchidos.
    So le o sistema (nao instala nada) — roda fora da UI thread para o dialogo de
    previa abrir ja sabendo o que falta, sem congelar a interface.
    """

    probed = Signal(object)  # list[StepPlan] = [(step_cls, step, tasks), ...]

    def __init__(self, step_classes: list[type[Step]], logger, *, run_dir: Path) -> None:
        super().__init__()
        self._step_classes = list(step_classes)
        self._logger = logger
        self._run_dir = run_dir

    def run(self) -> None:  # noqa: D401 (override QThread.run)
        from ..planning import collect_plans

        def build(step_cls: type[Step]) -> Step:
            return build_gui_step(
                step_cls, self._logger, dry_run=True, askpass=None, interactive_executor=None, run_dir=self._run_dir
            )

        plans = collect_plans(self._step_classes, self._logger, build)
        self.probed.emit(plans)


class StepWorker(QThread):
    resultReady = Signal(object)  # StepRunResult
    # (status, mensagem): status segue o vocabulario do CLI (blocked/manual/failed)
    # para a janela registrar a falha no resumo, como o run_steps faz no terminal.
    failed = Signal(str, str)

    def __init__(
        self,
        step_cls: type[Step],
        action: str,
        logger,
        *,
        askpass: str | None,
        interactive_executor: InteractiveExecutor | None,
        run_dir: Path,
        selection: tuple[str, ...] | None = None,
        force_keys: frozenset[str] | None = None,
    ) -> None:
        super().__init__()
        self._step_cls = step_cls
        self._action = action
        self._logger = logger
        self._askpass = askpass
        self._interactive_executor = interactive_executor
        self._run_dir = run_dir
        # Selecao de itens (chaves de StepTask) injetada pela GUI: quando presente,
        # o apply nao abre o checkbox proprio e roda exatamente esses itens.
        # force_keys marca os que devem reinstalar mesmo ja estando aplicados.
        self._selection = selection
        self._force_keys = force_keys or frozenset()
        # Runner da execucao corrente (para o botao Parar chamar request_abort).
        self.active_runner: Runner | None = None

    def stop(self) -> None:
        """Pede o cancelamento cooperativo do comando em execucao."""
        runner = self.active_runner
        if runner is not None:
            runner.request_abort()

    def run(self) -> None:  # noqa: D401 (override QThread.run)
        try:
            result = self._run_action()
            self.resultReady.emit(result)
        except PrivilegeEscalationBlockedError as exc:
            self.failed.emit("blocked", str(exc))
        except CommandInterruptedError as exc:
            self.failed.emit("blocked", str(exc))
        except PromptInterruptedError as exc:
            self.failed.emit("manual", str(exc))
        except Exception as exc:  # noqa: BLE001 - reportar qualquer falha na UI
            self.failed.emit("failed", f"etapa falhou: {exc}")
        finally:
            self.active_runner = None

    def _run_action(self) -> StepRunResult:
        step = build_gui_step(
            self._step_cls,
            self._logger,
            dry_run=self._action == "dry-run",
            askpass=self._askpass,
            interactive_executor=self._interactive_executor,
            run_dir=self._run_dir,
        )
        if self._selection is not None:
            step.selection = self._selection
        if self._force_keys:
            step.force_keys = set(self._force_keys)
        self.active_runner = step.ctx.runner
        started = time.monotonic()
        dispatch_action(step, self._action)
        return finalize_result(step, self._action, time.monotonic() - started)
