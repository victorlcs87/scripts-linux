from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .core import (
    Color,
    CommandInterruptedError,
    Logger,
    PrivilegeEscalationBlockedError,
    PromptInterruptedError,
    Runner,
    UserInfo,
    badge,
    paint,
    select_many,
)

# Estados de uma tarefa dentro de uma etapa.
# aplicado    -> ja esta feito na maquina (vem pre-marcado no Aplicar)
# pendente    -> ainda nao foi feito
# acao        -> nao tem estado (ex.: "atualizar o sistema"); sempre pode rodar
# indisponivel-> nao faz sentido nesta maquina (ex.: gestos sem touchpad)
TASK_STATES = ("aplicado", "pendente", "acao", "indisponivel", "desconhecido")


@dataclass
class StepTask:
    """Uma coisa concreta que a etapa sabe fazer, com explicacao e deteccao propria.

    E a unidade que o usuario ve na lista do Aplicar: cada tarefa e marcavel
    individualmente, vem pre-marcada quando ja esta aplicada, e carrega o texto
    que explica exatamente o que ela faz.

    `detect` responde "isso ja esta feito?": devolve bool ou uma string (verdade +
    detalhe, ex.: "instalado via flatpak") que aparece ao lado do rotulo.
    """

    key: str
    label: str
    description: str = ""
    # Texto curto (uma linha) para o card estilo Flathub; a `description` longa
    # vira tooltip/detalhes. Vazio -> a UI deriva da primeira frase da description.
    short_description: str = ""
    # Apresentacao (opcional): `icon` e um caminho de asset local OU um id Flathub
    # que a GUI resolve para o icone; `category` colore o avatar de fallback.
    icon: str = ""
    category: str = ""
    run: Callable[[], None] | None = None
    detect: Callable[[], bool | str | None] | None = None
    stateless: bool = False
    available: bool = True
    unavailable_reason: str = ""
    # Tarefa que REMOVE coisas: nunca vem pre-marcada, nem no "Aplicar tudo".
    # Marcar tem de ser um ato deliberado do usuario.
    destructive: bool = False
    # Acao que existe mas nao deve vir pre-marcada automaticamente (ex.: fazer um
    # backup durante o "Aplicar tudo" nao faz sentido numa maquina recem-formatada).
    # Continua selecionavel manualmente; so nao entra na pre-selecao.
    autoselect: bool = True
    state: str = "desconhecido"
    detail: str = ""

    @property
    def runnable(self) -> bool:
        return self.run is not None and self.state != "indisponivel"

    @property
    def preselectable(self) -> bool:
        return not self.destructive and self.autoselect

    def menu_label(self) -> str:
        """Rotulo com o estado atual embutido, usado no checkbox e nos resumos."""
        aviso = " [DESTRUTIVO]" if self.destructive else ""
        if self.state == "indisponivel":
            reason = self.unavailable_reason or "nao se aplica a esta maquina"
            return f"{self.label} (indisponivel: {reason})"
        if self.state == "aplicado":
            return f"{self.label}{aviso} ({self.detail or 'ja instalado'})"
        if self.state == "acao":
            return f"{self.label}{aviso} ({self.detail or 'acao sob demanda'})"
        if self.state == "pendente":
            return f"{self.label}{aviso} ({self.detail or 'instalar'})"
        return f"{self.label}{aviso} ({self.detail or 'estado desconhecido'})"


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
    description = ""
    # Quando True, o apply/status default derivam o compliance (aplicado/pendente)
    # do estado das tarefas. Etapas cujo veredito vem de diagnostico proprio
    # (ex.: GPU) desligam isso e chamam seus proprios mark_*.
    compliance_from_plan = True
    # Modelo Flathub: quando True, um item ja instalado (state == "aplicado") NAO
    # e reexecutado por padrao — so roda se o usuario pedir (force_keys/Reinstalar).
    # Vale para etapas de catalogo (apps): reinstalar so a pedido. Etapas de config
    # idempotente/atualizador (GPU, fstab, Sunshine, AppImages...) deixam False para
    # poderem reaplicar/atualizar normalmente.
    skip_if_installed = False

    def __init__(self, ctx: StepContext) -> None:
        self.ctx = ctx
        self.result = StepResult()
        # Seleccao pre-resolvida (chaves de StepTask) vinda da tela consolidada do
        # "Aplicar tudo"; quando presente, o apply nao abre o checkbox proprio.
        self.selection: tuple[str, ...] | None = None
        # Marca tudo por padrao no checkbox (em vez de so o que ja esta aplicado).
        self.select_all = False
        # Chaves de StepTask que o usuario mandou REINSTALAR/reaplicar de proposito.
        # Um item ja aplicado so roda de novo se sua chave estiver aqui; caso
        # contrario o run_tasks pula (modelo Flathub: instalado nao reinstala sozinho).
        self.force_keys: set[str] = set()

    # ------------------------------------------------------------------
    # Contrato novo: a etapa declara suas tarefas e o resto vem de graca.
    # ------------------------------------------------------------------
    def tasks(self) -> list[StepTask]:
        """Tarefas que a etapa sabe executar. Etapa sem tarefas precisa de apply() proprio."""
        return []

    def plan(self) -> list[StepTask]:
        """Sonda a maquina e devolve as tarefas com `state`/`detail` preenchidos."""
        tasks = self.tasks()
        for task in tasks:
            self._probe(task)
        return tasks

    def _probe(self, task: StepTask) -> None:
        if not task.available:
            task.state = "indisponivel"
            return
        if task.stateless:
            task.state = "acao"
            return
        if task.detect is None:
            task.state = "desconhecido"
            return
        try:
            outcome = task.detect()
        except Exception as exc:  # sonda nunca derruba a etapa
            task.state = "desconhecido"
            task.detail = f"nao consegui verificar: {exc}"
            return
        if isinstance(outcome, str):
            task.state = "aplicado" if outcome else "pendente"
            if outcome:
                task.detail = outcome
        else:
            task.state = "aplicado" if outcome else "pendente"

    # ------------------------------------------------------------------
    # Acoes
    # ------------------------------------------------------------------
    def declares_tasks(self) -> bool:
        """Distingue 'etapa sem tarefas declaradas' de 'nenhuma tarefa se aplica agora'."""
        return type(self).tasks is not Step.tasks

    def apply(self) -> None:
        if not self.declares_tasks():
            raise NotImplementedError
        self.run_tasks(self.plan())

    def status(self) -> None:
        if not self.declares_tasks():
            self.ctx.logger.write("Status ainda nao implementado para esta etapa.")
            return
        self.report_plan(self.plan())

    def undo(self) -> None:
        self.ctx.logger.write("Undo nao disponivel para esta etapa.")

    # ------------------------------------------------------------------
    # Motor de tarefas (usado pelo apply default)
    # ------------------------------------------------------------------
    def choose_tasks(self, tasks: Sequence[StepTask]) -> list[StepTask]:
        """Decide quais tarefas rodar: selecao injetada, tudo, ou checkbox interativo.

        Modelo Flathub: vem pre-marcado o que FALTA (pendente/acao) — marcar significa
        instalar/executar. O que ja esta instalado vem desmarcado e nao e reaplicado
        por padrao; reinstalar e um ato deliberado (via force_keys/Reinstalar).
        """
        runnable = [task for task in tasks if task.runnable]
        if not runnable:
            return []
        if self.selection is not None:
            keys = set(self.selection)
            return [task for task in runnable if task.key in keys]
        if self.select_all:
            return [task for task in runnable if task.preselectable]
        if len(runnable) == 1 and runnable[0].preselectable and runnable[0].state != "aplicado":
            # Etapa de item unico ainda pendente: escolher a etapa ja e escolher o item.
            return list(runnable)
        preselected = [
            index for index, task in enumerate(runnable) if task.state in ("pendente", "acao") and task.preselectable
        ]
        indices = select_many(
            f"{type(self).title}: o que executar?",
            [task.menu_label() for task in runnable],
            self.ctx.logger,
            detail=explain_tasks(runnable),
            preselected=preselected,
        )
        return [runnable[index] for index in indices]

    def run_tasks(self, tasks: Sequence[StepTask]) -> None:
        logger = self.ctx.logger
        if not tasks:
            self.mark_skipped("Nada se aplica a esta maquina nesta etapa.")
            return
        for task in tasks:
            if task.state == "indisponivel":
                logger.write(f"{badge('pulado', Color.WARNING)} {task.menu_label()}")

        chosen = self.choose_tasks(tasks)
        if not chosen:
            self.mark_skipped("Nenhum item marcado; nada foi alterado.")
            if self.compliance_from_plan:
                self.report_plan(self.plan(), quiet=True)
                self._prefix_summary_with_outcome()
            return

        manual_before = self.result.manual_events
        failures: list[str] = []
        skipped_installed = 0
        for task in chosen:
            # Modelo Flathub (so em etapas de catalogo): item ja instalado nao
            # reinstala sozinho. So roda de novo se o usuario pediu (force_keys) —
            # protege ate instaladores nao idempotentes de reexecutar sem querer.
            if self.skip_if_installed and task.state == "aplicado" and task.key not in self.force_keys:
                logger.write(
                    f"{badge('pulado', Color.INFO)} {task.label}: ja instalado "
                    "(use Reinstalar para forcar a reaplicacao)"
                )
                skipped_installed += 1
                continue
            logger.write("")
            logger.write(paint(f"-> {task.label}", Color.TITLE))
            if task.description:
                logger.write(paint(task.description, Color.MUTED))
            try:
                assert task.run is not None
                task.run()
            except (
                PrivilegeEscalationBlockedError,
                CommandInterruptedError,
                PromptInterruptedError,
            ):
                raise
            except Exception as exc:
                failures.append(task.label)
                logger.write(f"{badge('erro', Color.ERROR)} {task.label}: {exc}")
                self.add_hint(f"revise '{task.label}': {exc}")

        executed = len(chosen) - skipped_installed
        if failures:
            self.mark_done(f"Concluido com falhas em: {', '.join(failures)}.")
        elif self.result.manual_events > manual_before:
            self.mark_manual("A etapa dependeu de interacao manual.")
        elif executed == 0 and skipped_installed:
            self.mark_skipped(f"{skipped_installed} item(ns) ja instalado(s); nada a fazer.")
        elif not self.result.message:
            # Nenhuma tarefa se pronunciou (mark_skipped/mark_done proprio): resumo generico.
            extra = f" ({skipped_installed} ja instalado(s))" if skipped_installed else ""
            self.mark_done(f"{executed} item(ns) processado(s){extra}.")

        # Reconfere o estado real depois de agir.
        if self.compliance_from_plan:
            self.report_plan(self.plan(), quiet=True, attention=failures)
            self._prefix_summary_with_outcome()

    def _prefix_summary_with_outcome(self) -> None:
        """No apply, o resumo tem de dizer o que FOI FEITO antes do estado da maquina.

        Sem isso um 'nada foi alterado' aparecia no resumo final como
        '2/3 itens aplicados', que se le como se a etapa tivesse feito o trabalho.
        """
        estado = self.result.summary
        feito = self.result.message
        if feito and estado:
            self.result.summary = f"{feito} Estado atual: {estado}"

    def report_plan(
        self,
        tasks: Sequence[StepTask],
        *,
        quiet: bool = False,
        attention: Sequence[str] | None = None,
    ) -> None:
        """Traduz o plano sondado em compliance + listas por item."""
        logger = self.ctx.logger
        applied = [task.label for task in tasks if task.state == "aplicado"]
        missing = [task.label for task in tasks if task.state == "pendente"]
        unknown = [task.label for task in tasks if task.state == "desconhecido"]
        attention_items = list(attention or []) + unknown

        if not quiet:
            for task in tasks:
                tone = {
                    "aplicado": Color.SUCCESS,
                    "pendente": Color.WARNING,
                    "acao": Color.INFO,
                    "indisponivel": Color.MUTED,
                }.get(task.state, Color.ERROR)
                logger.write(f"{badge(task.state, tone)} {task.menu_label()}")
                if task.description:
                    logger.write(paint(f"  {task.description}", Color.MUTED))

        total = len(applied) + len(missing)
        if attention_items:
            self.mark_attention(
                f"{len(applied)} de {total} item(ns) presentes; revise o que precisa de atencao.",
                attention=attention_items,
            )
        elif missing:
            self.mark_pending(f"{len(applied)} de {total} item(ns) presentes na maquina.", missing=missing)
        else:
            self.mark_applied(
                f"Todos os {total} item(ns) presentes na maquina." if total else "Nada a aplicar.", items=applied
            )

    # ------------------------------------------------------------------
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


def explain_tasks(tasks: Sequence[StepTask]) -> str:
    """Explica, item a item, o que sera feito — compacto (uma linha curta por item).

    Instalado vem marcado; instalar/executar mostra o texto curto. A descricao
    longa completa fica no Status, para nao inundar o seletor.
    """
    lines: list[str] = []
    for task in tasks:
        marcador = "✓" if task.state == "aplicado" else "○"
        curto = task.short_description or (task.description.split(". ")[0] if task.description else "")
        sufixo = f" — {curto}" if curto else ""
        lines.append(f"{marcador} {task.label}{sufixo}")
    return "\n".join(lines)
