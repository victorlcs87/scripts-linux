"""Despacho de acoes de step compartilhado entre CLI e GUI.

Unica fonte da verdade para: mapear a acao (apply/dry-run/status/undo) no
metodo do step, preencher mensagens/resumos default e montar o StepRunResult.
Os frontends apenas constroem o Step (com o Runner que quiserem) e delegam aqui.
"""

from __future__ import annotations

from .core import StepRunResult
from .steps_base import Step

ACTIONS = ("apply", "dry-run", "status", "undo")


def dispatch_action(step: Step, action: str) -> None:
    """Executa a acao no step. O modo dry vem do Runner injetado, nao daqui."""
    if action in ("apply", "dry-run"):
        step.apply()
    elif action == "status":
        step.status()
    elif action == "undo":
        step.undo()
    else:
        raise ValueError(f"acao invalida: {action}")


def default_step_message(action: str, status: str) -> str:
    if status == "skipped":
        return "Nada novo para fazer nesta etapa."
    if status == "manual":
        return "A etapa dependeu de interacao manual."
    if action == "status":
        return "Status coletado."
    if action == "dry-run":
        return "Dry-run concluido."
    if action == "undo":
        return "Undo concluido."
    return "Etapa concluida."


def default_step_summary(action: str, result) -> str:
    if result.summary:
        return result.summary
    if action == "status":
        if result.compliance == "aplicado":
            return "Etapa aplicada."
        if result.compliance == "pendente":
            return "Ha itens pendentes nesta etapa."
        if result.compliance == "atencao":
            return "A etapa requer atencao."
        return "Status coletado."
    if action == "dry-run":
        return "Dry-run concluido."
    if action == "undo":
        return "Undo concluido."
    return result.message or "Etapa concluida."


def finalize_result(step: Step, action: str, elapsed: float) -> StepRunResult:
    """Preenche defaults e congela o resultado do step num StepRunResult.

    Copia as listas para o resultado nao mudar se o step for reutilizado.
    """
    if not step.result.message:
        step.result.message = default_step_message(action, step.result.status)
    if not step.result.summary:
        step.result.summary = default_step_summary(action, step.result)
    return StepRunResult(
        step_id=type(step).id,
        title=type(step).title,
        status=step.result.status,
        message=step.result.summary,
        compliance=step.result.compliance,
        duration_seconds=elapsed,
        applied_items=list(step.result.applied_items),
        missing_items=list(step.result.missing_items),
        attention_items=list(step.result.attention_items),
        hints=list(step.result.hints),
    )
