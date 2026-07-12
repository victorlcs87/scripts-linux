from __future__ import annotations

import sys
import time
from collections.abc import Callable
from pathlib import Path

from .core import (
    Color,
    CommandInterruptedError,
    Logger,
    MenuOption,
    PrivilegeEscalationBlockedError,
    PromptInterruptedError,
    Runner,
    StepRunResult,
    badge,
    detect_user,
    divider,
    format_elapsed,
    is_root,
    no_new_privs_enabled,
    paint,
    progress_bar,
    prompt_user,
)
from .dispatch import dispatch_action, finalize_result
from .planning import collect_plans, prompt_global_selection, render_step_explanation
from .steps import ALL_STEPS
from .steps_base import Step, StepContext
from .tui import TuiDependencyError, choose_multiple, choose_option

ROOT = Path(__file__).resolve().parent.parent

# Mapa unico status->cor (execucao) e compliance->cor (status), usado por todos
# os renderizadores de resumo (CLI e GUI importam daqui).
STATUS_TONES = {
    "done": Color.SUCCESS,
    "skipped": Color.WARNING,
    "manual": Color.WARNING,
    "failed": Color.ERROR,
    "blocked": Color.ERROR,
}
COMPLIANCE_TONES = {
    "aplicado": Color.SUCCESS,
    "pendente": Color.WARNING,
    "atencao": Color.ERROR,
}


def clear_screen() -> None:
    if sys.stdout.isatty():
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()
    else:
        print("\n" * 3)


def build_step(step_cls: type[Step], logger: Logger, *, dry_run: bool = False) -> Step:
    user = detect_user()
    ctx = StepContext(
        root=ROOT,
        run_dir=Path.cwd(),
        user=user,
        logger=logger,
        runner=Runner(logger, dry_run=dry_run),
    )
    return step_cls(ctx)


def step_by_id(step_id: str) -> type[Step] | None:
    for step in ALL_STEPS:
        if step.id == step_id:
            return step
    return None


def run_action(
    step_cls: type[Step],
    action: str,
    logger: Logger,
    *,
    configure: Callable[[Step], None] | None = None,
) -> StepRunResult:
    step = build_step(step_cls, logger, dry_run=action == "dry-run")
    if configure is not None:
        configure(step)
    started = time.monotonic()
    dispatch_action(step, action)
    return finalize_result(step, action, time.monotonic() - started)


def run_action_safe(
    step_cls: type[Step],
    action: str,
    logger: Logger,
    *,
    configure: Callable[[Step], None] | None = None,
) -> StepRunResult | None:
    # A pausa "ENTER para voltar" acontece UMA vez, no finally, para todo desfecho.
    try:
        clear_screen()
        result = run_action(step_cls, action, logger, configure=configure)
        render_step_summary(logger, action, result)
        return result
    except (PrivilegeEscalationBlockedError, CommandInterruptedError) as exc:
        logger.write(f"{badge('erro', Color.ERROR)} {exc}")
    except PromptInterruptedError as exc:
        logger.write(f"{badge('aviso', Color.WARNING)} {exc}")
    except Exception as exc:
        logger.write(f"{badge('erro', Color.ERROR)} etapa falhou: {exc}")
    finally:
        prompt_return_to_menu(logger)
    return None


def synthetic_result(step_cls: type[Step], status: str, exc: Exception) -> StepRunResult:
    """Resultado sintetico para etapa que falhou/foi bloqueada antes de concluir.

    Tambem usado pela GUI para registrar falhas no resumo final.
    """
    return StepRunResult(
        step_id=step_cls.id,
        title=step_cls.title,
        status=status,
        message=str(exc),
        compliance="atencao",
        duration_seconds=0.0,
    )


def plan_selection(steps: list[type[Step]], logger: Logger, *, select_all: bool) -> dict[str, tuple[str, ...]] | None:
    """Sonda as etapas e abre a tela unica de selecao. None = usuario nao marcou nada."""
    clear_screen()
    logger.write(paint("Verificando o que ja esta aplicado nesta maquina...", Color.MUTED))
    plans = collect_plans(steps, logger, lambda step_cls: build_step(step_cls, logger))
    if not plans:
        return {}
    try:
        selection = prompt_global_selection(plans, logger, select_all=select_all)
    except (PromptInterruptedError, TuiDependencyError) as exc:
        logger.write(f"{badge('aviso', Color.WARNING)} {exc}")
        return None
    if not any(selection.values()):
        logger.write(f"{badge('aviso', Color.WARNING)} Nenhum item marcado; nada a executar.")
        prompt_return_to_menu(logger)
        return None
    return selection


def run_all(action: str, logger: Logger) -> None:
    steps = list(ALL_STEPS)
    if action != "apply":
        run_steps(steps, action, logger)
        return
    # Aplicar tudo: uma tela so, com tudo pre-marcado.
    selection = plan_selection(steps, logger, select_all=True)
    if selection is None:
        return
    run_steps(steps, action, logger, selection=selection)


def run_steps(
    steps: list[type[Step]],
    action: str,
    logger: Logger,
    *,
    selection: dict[str, tuple[str, ...]] | None = None,
) -> None:
    clear_screen()
    total = len(steps)
    results: list[StepRunResult] = []
    overall_started = time.monotonic()

    def configure(step: Step) -> None:
        if selection is not None:
            step.selection = selection.get(type(step).id, ())

    for index, step_cls in enumerate(steps, 1):
        percent = index / total
        logger.write("")
        logger.write(paint(progress_bar(index, total), Color.ACCENT))
        logger.write(
            paint(f"Etapa {index:02d}/{total:02d}  |  {int(percent * 100):02d}%  |  modo: {action}", Color.MUTED)
        )
        logger.write(paint(step_cls.title, Color.TITLE))
        try:
            result = run_action(step_cls, action, logger, configure=configure)
            results.append(result)
        except PrivilegeEscalationBlockedError as exc:
            logger.write(f"{badge('erro', Color.ERROR)} {exc}")
            logger.write(
                f"{badge('dica', Color.WARNING)} etapas que precisam de sudo nao podem continuar neste ambiente."
            )
            results.append(synthetic_result(step_cls, "blocked", exc))
            break
        except CommandInterruptedError as exc:
            logger.write(f"{badge('erro', Color.ERROR)} {exc}")
            results.append(synthetic_result(step_cls, "blocked", exc))
            break
        except PromptInterruptedError as exc:
            logger.write(f"{badge('aviso', Color.WARNING)} {exc}")
            results.append(synthetic_result(step_cls, "manual", exc))
            break
        except Exception as exc:
            logger.write(f"{badge('erro', Color.ERROR)} etapa falhou: {exc}")
            results.append(synthetic_result(step_cls, "failed", exc))
            if action in {"apply", "dry-run"}:
                try:
                    prompt_user(
                        "Pressione ENTER para continuar com a proxima etapa ou Ctrl+C para parar",
                        logger,
                        detail="O fluxo esta pausado aguardando sua decisao.",
                        prompt_label="ENTER",
                    )
                except PromptInterruptedError as prompt_exc:
                    logger.write(f"{badge('aviso', Color.WARNING)} {prompt_exc}")
                    break
    if action == "status":
        render_status_overview(logger, results, total, time.monotonic() - overall_started)
    else:
        render_run_summary(logger, action, results, total, time.monotonic() - overall_started)
    prompt_return_to_menu(logger)


def choose_step(logger: Logger, steps: list[type[Step]] | None = None) -> type[Step] | None:
    steps = list(steps) if steps is not None else list(ALL_STEPS)
    # key == posicao (1..N) para o fallback numerado; exibe so o titulo.
    options = [MenuOption(str(index + 1), step_cls.title) for index, step_cls in enumerate(steps)]
    clear_screen()
    try:
        index = choose_option(
            title="Escolha a etapa que voce quer abrir",
            logger=logger,
            prompt="Digite o numero da etapa",
            options=options,
            detail="O reforja esta aguardando sua escolha de etapa.",
            prompt_label="Etapa",
        )
    except PromptInterruptedError as exc:
        logger.write(f"{badge('aviso', Color.WARNING)} {exc}")
        return None
    except TuiDependencyError as exc:
        logger.write(f"{badge('erro', Color.ERROR)} {exc}")
        return None
    return steps[index]


def choose_action(logger: Logger, steps: list[type[Step]]) -> str | None:
    # Prompt compacto na MESMA tela da selecao: Enter = Aplicar (default).
    options = [
        MenuOption("1", "Aplicar (padrao)"),
        MenuOption("2", "Status"),
        MenuOption("3", "Undo"),
        MenuOption("4", "Cancelar"),
    ]
    actions = ["apply", "status", "undo", None]
    try:
        index = choose_option(
            title=f"Acao para: {', '.join(step_cls.title for step_cls in steps)}",
            logger=logger,
            prompt="Qual acao executar (Enter = Aplicar)",
            options=options,
            prompt_label="Acao",
        )
    except (PromptInterruptedError, TuiDependencyError) as exc:
        logger.write(f"{badge('aviso', Color.WARNING)} {exc}")
        return None
    return actions[index]


def select_and_run(logger: Logger) -> None:
    # key == posicao (1..N); exibe so o titulo, sem numeracao de etapa.
    options = [MenuOption(str(index + 1), step_cls.title) for index, step_cls in enumerate(ALL_STEPS)]
    clear_screen()
    try:
        indices = choose_multiple(
            title="Executar etapas",
            logger=logger,
            prompt="Quais etapas",
            options=options,
            detail="Marque com espaco as etapas desejadas. Em seguida escolha a acao (Enter = Aplicar).",
        )
    except (PromptInterruptedError, TuiDependencyError) as exc:
        logger.write(f"{badge('aviso', Color.WARNING)} {exc}")
        return
    chosen = [ALL_STEPS[i] for i in indices]
    if not chosen:
        logger.write(f"{badge('aviso', Color.WARNING)} Nenhuma etapa marcada; nada a executar.")
        return
    # Sem limpar a tela: a escolha da acao acontece logo abaixo da selecao.
    action = choose_action(logger, chosen)
    if action is None:
        return
    if action == "apply":
        # Antes de aplicar: mostra os itens de cada etapa, ja marcando o que existe hoje.
        selection = plan_selection(chosen, logger, select_all=False)
        if selection is None:
            return
        run_steps(chosen, action, logger, selection=selection)
        return
    run_steps(chosen, action, logger)


def describe_step(step_cls: type[Step], logger: Logger) -> list[str]:
    """Explicacao completa da etapa (descricao + tarefas com o estado atual)."""
    try:
        step = build_step(step_cls, logger)
        tasks = step.plan()
    except Exception as exc:
        logger.write(f"{badge('aviso', Color.WARNING)} nao consegui sondar esta etapa: {exc}")
        tasks = []
    return render_step_explanation(step_cls, tasks)


def step_menu(step_cls: type[Step], logger: Logger) -> None:
    options = [
        MenuOption("1", "Apply"),
        MenuOption("2", "Status"),
        MenuOption("3", "Undo"),
        MenuOption("4", "Sair"),
    ]
    while True:
        clear_screen()
        # Explica por completo o que a etapa faz e como ela esta agora, antes de agir.
        explanation = describe_step(step_cls, logger)
        for line in explanation:
            logger.write(line)
        logger.write("")
        try:
            option = choose_option(
                title=step_cls.title,
                logger=logger,
                prompt="Escolha uma acao para esta etapa",
                options=options,
                footer="Durante comandos longos, o reforja mostra atividade viva para voce saber que nao travou.",
                detail=step_cls.description or "O reforja esta aguardando sua escolha.",
                prompt_label="Escolha",
            )
        except PromptInterruptedError as exc:
            logger.write(f"{badge('aviso', Color.WARNING)} {exc}")
            return
        except TuiDependencyError as exc:
            logger.write(f"{badge('erro', Color.ERROR)} {exc}")
            return
        if option == 0:
            run_action_safe(step_cls, "apply", logger)
        elif option == 1:
            run_action_safe(step_cls, "status", logger)
        elif option == 2:
            run_action_safe(step_cls, "undo", logger)
        elif option == 3:
            return


def install_reforja_gui(logger: Logger) -> None:
    """Instala/atualiza a GUI do Reforja no sistema: baixa o AppImage mais recente
    das GitHub Releases e cria o atalho .desktop + icone (via passo 15, so o Reforja)."""
    step_cls = step_by_id("15")
    if step_cls is None:
        logger.write(f"{badge('erro', Color.ERROR)} etapa de AppImages nao encontrada.")
        return

    def only_reforja(step: Step) -> None:
        step.preselect_names = ("Reforja",)

    run_action_safe(step_cls, "apply", logger, configure=only_reforja)


def main_menu(logger: Logger) -> None:
    options = [
        MenuOption("1", "Aplicar tudo"),
        MenuOption("2", "Status geral"),
        MenuOption("3", "Executar etapas..."),
        MenuOption("4", "Instalar GUI do Reforja no sistema"),
        MenuOption("5", "Sair"),
    ]
    while True:
        clear_screen()
        try:
            option = choose_option(
                title="Reforja pos-formatacao Linux/KDE",
                logger=logger,
                prompt="Escolha uma opcao do menu principal",
                options=options,
                footer="Tema neon ativo quando o terminal suporta ANSI. Use NO_COLOR=1 para desativar as cores.",
                detail="Quando o menu esta aqui, o reforja esta esperando voce e nao travado.",
                prompt_label="Escolha",
            )
        except PromptInterruptedError as exc:
            logger.write(f"{badge('aviso', Color.WARNING)} {exc}")
            return
        except TuiDependencyError as exc:
            logger.write(f"{badge('erro', Color.ERROR)} {exc}")
            return
        if option == 0:
            run_all("apply", logger)
        elif option == 1:
            run_all("status", logger)
        elif option == 2:
            select_and_run(logger)
        elif option == 3:
            install_reforja_gui(logger)
        elif option == 4:
            return


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    logger = Logger(Path.cwd(), "00-pos-formatacao-cachyos")
    if is_root():
        logger.write(
            f"{badge('erro', Color.ERROR)} nao execute como root. Use usuario normal; sudo sera chamado quando necessario."
        )
        return 1
    if no_new_privs_enabled():
        logger.write(f"{badge('aviso', Color.WARNING)} este terminal bloqueia sudo (NoNewPrivs=1).")
        logger.write(
            "Status continua funcionando, mas Apply de etapas privilegiadas precisa ser executado em uma sessao normal do sistema."
        )
    if argv and argv[0] == "step":
        if len(argv) < 2:
            logger.write("Uso: python -m reforja.cli step ID [apply|status|undo|menu]")
            return 1
        step_cls = step_by_id(argv[1])
        if not step_cls:
            logger.write(f"Etapa nao encontrada: {argv[1]}")
            return 1
        action = argv[2] if len(argv) > 2 else "menu"
        try:
            if action == "menu":
                step_menu(step_cls, logger)
            else:
                run_action(step_cls, action, logger)
        except (
            PrivilegeEscalationBlockedError,
            CommandInterruptedError,
            PromptInterruptedError,
            TuiDependencyError,
        ) as exc:
            logger.write(f"{badge('erro', Color.ERROR)} {exc}")
            return 1
        return 0
    main_menu(logger)
    return 0


def render_step_summary(logger: Logger, action: str, result: StepRunResult) -> None:
    tone = STATUS_TONES.get(result.status, Color.INFO)
    logger.write("")
    logger.write(divider(char="#", tone=Color.TITLE))
    logger.write(paint("Resumo da etapa", Color.TITLE))
    logger.write(
        paint(
            f"Modo: {action}  |  Etapa: {result.title}  |  Duracao: {format_elapsed(result.duration_seconds)}",
            Color.MUTED,
        )
    )
    logger.write(divider(char="-", tone=Color.BOX))
    logger.write(f"{badge(result.status, tone)} {result.message}")
    logger.write(divider(char="#", tone=Color.TITLE))


def render_status_overview(
    logger: Logger,
    results: list[StepRunResult],
    total_steps: int,
    duration_seconds: float,
) -> None:
    counts = {"aplicado": 0, "pendente": 0, "atencao": 0}
    classified: list[tuple[StepRunResult, str]] = []
    for item in results:
        compliance = item.compliance if item.compliance in counts else "atencao"
        counts[compliance] += 1
        classified.append((item, compliance))

    # Acionaveis primeiro: atencao > pendente > aplicado (ordem original dentro do grupo).
    priority = {"atencao": 0, "pendente": 1, "aplicado": 2}
    ordered = sorted(enumerate(classified), key=lambda pair: (priority[pair[1][1]], pair[0]))
    tones = COMPLIANCE_TONES

    logger.write("")
    logger.write(divider(char="#", tone=Color.TITLE))
    logger.write(paint("Resumo inteligente do status", Color.TITLE))
    logger.write(paint(f"Duracao total: {format_elapsed(duration_seconds)}  |  Log: {logger.path}", Color.MUTED))
    logger.write(divider(char="-", tone=Color.BOX))
    logger.write(f"{badge('aplicado', Color.SUCCESS)} {counts['aplicado']} etapa(s) aplicadas")
    logger.write(f"{badge('pendente', Color.WARNING)} {counts['pendente']} etapa(s) pendentes")
    logger.write(f"{badge('atencao', Color.ERROR)} {counts['atencao']} etapa(s) com atencao")
    logger.write(paint(f"Executadas: {len(results)}/{total_steps}", Color.ACCENT))
    logger.write(divider(char="-", tone=Color.BOX))

    for _, (item, compliance) in ordered:
        tone = tones[compliance]
        logger.write(f"{badge(compliance, tone)} {item.title}")
        logger.write(paint(f"  {item.message}", Color.MUTED))
        if compliance == "aplicado":
            if item.applied_items:
                logger.write(paint(f"  feito: {', '.join(item.applied_items)}", Color.SUCCESS))
        else:
            for missing in item.missing_items:
                logger.write(paint(f"  - falta: {missing}", Color.WARNING))
            for attention in item.attention_items:
                logger.write(paint(f"  - atencao: {attention}", Color.ERROR))
        for hint in item.hints:
            logger.write(f"  {paint('->', Color.ACCENT)} {paint(f'sugestao: {hint}', Color.ACCENT)}")

    # Proximos passos: checklist do que ainda nao esta aplicado.
    pending = [(item, compliance) for _, (item, compliance) in ordered if compliance != "aplicado"]
    logger.write(divider(char="-", tone=Color.BOX))
    if not pending:
        logger.write(f"{badge('ok', Color.SUCCESS)} Tudo aplicado — nada pendente.")
    else:
        logger.write(paint("Proximos passos", Color.TITLE))
        for item, compliance in pending:
            if item.hints:
                acao = item.hints[0]
            elif item.missing_items:
                acao = f"resolver: {', '.join(item.missing_items)}"
            elif item.attention_items:
                acao = f"revisar: {', '.join(item.attention_items)}"
            else:
                acao = "rode Aplicar nesta etapa"
            logger.write(f"{badge(compliance, tones[compliance])} {item.title}")
            logger.write(paint(f"  {acao}", Color.MUTED))
    logger.write(divider(char="#", tone=Color.TITLE))


def prompt_return_to_menu(logger: Logger) -> None:
    try:
        prompt_user(
            "Pressione ENTER para voltar ao menu",
            logger,
            detail="O reforja esta pausado para voce conseguir ler o resumo.",
            prompt_label="ENTER",
        )
    except PromptInterruptedError as exc:
        logger.write(f"{badge('aviso', Color.WARNING)} {exc}")


def render_run_summary(
    logger: Logger,
    action: str,
    results: list[StepRunResult],
    total_steps: int,
    duration_seconds: float,
) -> None:
    counts = {
        "done": sum(1 for item in results if item.status == "done"),
        "skipped": sum(1 for item in results if item.status == "skipped"),
        "failed": sum(1 for item in results if item.status == "failed"),
        "manual": sum(1 for item in results if item.status == "manual"),
        "blocked": sum(1 for item in results if item.status == "blocked"),
    }
    logger.write("")
    logger.write(divider(char="#", tone=Color.TITLE))
    logger.write(paint("Resumo final do fluxo", Color.TITLE))
    logger.write(
        paint(
            f"Modo: {action}  |  Duracao total: {format_elapsed(duration_seconds)}  |  Log: {logger.path}", Color.MUTED
        )
    )
    logger.write(divider(char="-", tone=Color.BOX))
    logger.write(f"{badge('done', Color.SUCCESS)} {counts['done']} concluida(s)")
    logger.write(f"{badge('skipped', Color.WARNING)} {counts['skipped']} pulada(s)")
    logger.write(f"{badge('manual', Color.WARNING)} {counts['manual']} com interacao manual")
    logger.write(f"{badge('failed', Color.ERROR)} {counts['failed']} falha(s)")
    logger.write(f"{badge('blocked', Color.ERROR)} {counts['blocked']} bloqueada(s)")
    logger.write(paint(f"Executadas: {len(results)}/{total_steps}", Color.ACCENT))
    logger.write(divider(char="-", tone=Color.BOX))
    for item in results:
        tone = STATUS_TONES.get(item.status, Color.INFO)
        logger.write(
            f"{badge(item.status, tone)} {item.title}  "
            f"{paint(f'({format_elapsed(item.duration_seconds)})', Color.MUTED)}"
        )
        if item.message:
            logger.write(paint(item.message, Color.MUTED))
    logger.write(divider(char="#", tone=Color.TITLE))


if __name__ == "__main__":
    raise SystemExit(main())
