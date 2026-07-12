"""Sondagem e selecao de tarefas ANTES de executar as etapas.

Uma etapa declara suas tarefas (`Step.tasks()`); aqui a maquina e sondada para
saber o que ja esta aplicado e o usuario escolhe, numa tela unica, o que sera
feito. O resultado e um mapa `{step_id: (chaves_das_tarefas,)}` que os frontends
injetam em `Step.selection`, fazendo o apply pular o checkbox interno.

Compartilhado por CLI e GUI: a selecao passa pelo `core.select_many`, que ja
roteia para o checkbox do terminal ou para o dialogo grafico.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from .core import Color, Logger, badge, paint, select_many
from .steps_base import Step, StepTask

# Uma etapa sondada: a classe, a instancia usada na sondagem e suas tarefas.
StepPlan = tuple[type[Step], Step, list[StepTask]]


def collect_plans(
    step_classes: Sequence[type[Step]],
    logger: Logger,
    build: Callable[[type[Step]], Step],
) -> list[StepPlan]:
    """Sonda cada etapa e devolve suas tarefas com o estado atual preenchido."""
    plans: list[StepPlan] = []
    for step_cls in step_classes:
        try:
            step = build(step_cls)
            tasks = step.plan()
        except Exception as exc:  # uma etapa que nao sonda nao pode derrubar o resto
            logger.write(f"{badge('aviso', Color.WARNING)} nao consegui sondar '{step_cls.title}': {exc}")
            continue
        if tasks:
            plans.append((step_cls, step, tasks))
    return plans


def render_step_explanation(step_cls: type[Step], tasks: Sequence[StepTask]) -> list[str]:
    """Bloco que explica por completo o que a etapa faz e como ela esta agora."""
    lines = [paint(step_cls.title, Color.TITLE)]
    if step_cls.description:
        lines.append(paint(step_cls.description, Color.MUTED))
    if not tasks:
        lines.append(paint("Nada desta etapa se aplica a esta maquina.", Color.MUTED))
        return lines
    lines.append("")
    lines.append(paint("O que esta etapa faz, item a item:", Color.ACCENT))
    tones = {
        "aplicado": Color.SUCCESS,
        "pendente": Color.WARNING,
        "acao": Color.INFO,
        "indisponivel": Color.MUTED,
    }
    for task in tasks:
        lines.append(f"{badge(task.state, tones.get(task.state, Color.ERROR))} {task.menu_label()}")
        if task.description:
            lines.append(paint(f"    {task.description}", Color.MUTED))
    return lines


def describe_step_plain(step_cls: type[Step], tasks: Sequence[StepTask]) -> list[str]:
    """Mesma explicacao, em texto puro (sem ANSI) — para o console da GUI.

    Recebe as tarefas SEM sondagem quando o chamador nao quer disparar comandos
    (algumas sondas pedem sudo); nesse caso o estado nao e exibido, e ele aparece
    pre-marcado no dialogo do Aplicar.
    """
    lines = [step_cls.description or "Sem descricao disponivel para esta etapa."]
    if not tasks:
        return lines
    lines.append("")
    lines.append("O que esta etapa faz, item a item:")
    for task in tasks:
        estado = "" if task.state == "desconhecido" else f"  [{task.state}]"
        if task.state == "indisponivel" and task.unavailable_reason:
            estado = f"  [indisponivel: {task.unavailable_reason}]"
        lines.append(f"  - {task.label}{estado}")
        if task.description:
            lines.append(f"      {task.description}")
    return lines


def prompt_global_selection(
    plans: Sequence[StepPlan],
    logger: Logger,
    *,
    select_all: bool,
) -> dict[str, tuple[str, ...]]:
    """Tela unica com os itens de TODAS as etapas; devolve {step_id: chaves marcadas}.

    `select_all=True` (Aplicar tudo) marca tudo por padrao — o comportamento normal
    e instalar tudo. Caso contrario vem marcado apenas o que JA esta aplicado.
    """
    labels: list[str] = []
    details: list[str] = []
    origins: list[tuple[str, str]] = []  # (step_id, task_key) na mesma ordem dos labels
    preselected: list[int] = []

    for step_cls, _step, tasks in plans:
        for task in tasks:
            if not task.runnable:
                continue
            index = len(labels)
            labels.append(f"{step_cls.title}  >  {task.menu_label()}")
            details.append(f"- {step_cls.title} > {task.menu_label()}")
            if task.description:
                details.append(f"    {task.description}")
            origins.append((step_cls.id, task.key))
            # Tarefa destrutiva nunca vem marcada — nem no "Aplicar tudo".
            if task.preselectable and (select_all or task.state == "aplicado"):
                preselected.append(index)

    if not labels:
        return {}

    indices = select_many(
        "O que voce quer aplicar?",
        labels,
        logger,
        detail="\n".join(details),
        preselected=preselected,
    )

    selection: dict[str, tuple[str, ...]] = {step_cls.id: () for step_cls, _step, _tasks in plans}
    for index in indices:
        step_id, task_key = origins[index]
        selection[step_id] = (*selection[step_id], task_key)
    return selection
