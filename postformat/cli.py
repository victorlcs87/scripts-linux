from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from .core import (
    Color,
    CommandInterruptedError,
    Logger,
    MenuOption,
    PromptInterruptedError,
    PrivilegeEscalationBlockedError,
    Runner,
    StepRunResult,
    badge,
    detect_user,
    divider,
    is_root,
    no_new_privs_enabled,
    paint,
    progress_bar,
    prompt_user,
    format_elapsed,
)
from .steps import ALL_STEPS
from .steps_base import Step, StepContext
from .tui import TuiDependencyError, choose_option


ROOT = Path(__file__).resolve().parent.parent


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


def run_action(step_cls: type[Step], action: str, logger: Logger) -> StepRunResult:
    dry = action == "dry-run"
    step = build_step(step_cls, logger, dry_run=dry)
    started = time.monotonic()
    if action == "apply":
        step.apply()
    elif action == "dry-run":
        step.apply()
    elif action == "status":
        step.status()
    elif action == "undo":
        step.undo()
    else:
        raise ValueError(f"acao invalida: {action}")
    if not step.result.message:
        step.result.message = default_step_message(action, step.result.status)
    if not step.result.summary:
        step.result.summary = default_step_summary(action, step.result)
    return StepRunResult(
        step_id=step_cls.id,
        title=step_cls.title,
        status=step.result.status,
        message=step.result.summary,
        compliance=step.result.compliance,
        duration_seconds=time.monotonic() - started,
    )


def run_action_safe(step_cls: type[Step], action: str, logger: Logger) -> StepRunResult | None:
    try:
        clear_screen()
        result = run_action(step_cls, action, logger)
        render_step_summary(logger, action, result)
        prompt_return_to_menu(logger)
        return result
    except PrivilegeEscalationBlockedError as exc:
        logger.write(f"{badge('erro', Color.ERROR)} {exc}")
    except CommandInterruptedError as exc:
        logger.write(f"{badge('erro', Color.ERROR)} {exc}")
    except PromptInterruptedError as exc:
        logger.write(f"{badge('aviso', Color.WARNING)} {exc}")
    except Exception as exc:
        logger.write(f"{badge('erro', Color.ERROR)} etapa falhou: {exc}")
        prompt_return_to_menu(logger)
    return None


def run_all(action: str, logger: Logger) -> None:
    clear_screen()
    total = len(ALL_STEPS)
    results: list[StepRunResult] = []
    overall_started = time.monotonic()
    for index, step_cls in enumerate(ALL_STEPS, 1):
        percent = index / total
        logger.write("")
        logger.write(paint(progress_bar(index, total), Color.ACCENT))
        logger.write(paint(f"Etapa {index:02d}/{total:02d}  |  {int(percent * 100):02d}%  |  modo: {action}", Color.MUTED))
        logger.write(f"{badge(step_cls.id, Color.TITLE)} {paint(step_cls.title, Color.TITLE)}")
        try:
            result = run_action(step_cls, action, logger)
            results.append(result)
        except PrivilegeEscalationBlockedError as exc:
            logger.write(f"{badge('erro', Color.ERROR)} {exc}")
            logger.write(f"{badge('dica', Color.WARNING)} etapas que precisam de sudo nao podem continuar neste ambiente.")
            results.append(
                StepRunResult(
                    step_id=step_cls.id,
                    title=step_cls.title,
                    status="blocked",
                    message=str(exc),
                    compliance="atencao",
                    duration_seconds=0.0,
                )
            )
            break
        except CommandInterruptedError as exc:
            logger.write(f"{badge('erro', Color.ERROR)} {exc}")
            results.append(
                StepRunResult(
                    step_id=step_cls.id,
                    title=step_cls.title,
                    status="blocked",
                    message=str(exc),
                    compliance="atencao",
                    duration_seconds=0.0,
                )
            )
            break
        except PromptInterruptedError as exc:
            logger.write(f"{badge('aviso', Color.WARNING)} {exc}")
            results.append(
                StepRunResult(
                    step_id=step_cls.id,
                    title=step_cls.title,
                    status="manual",
                    message=str(exc),
                    compliance="atencao",
                    duration_seconds=0.0,
                )
            )
            break
        except Exception as exc:
            logger.write(f"{badge('erro', Color.ERROR)} etapa falhou: {exc}")
            results.append(
                StepRunResult(
                    step_id=step_cls.id,
                    title=step_cls.title,
                    status="failed",
                    message=str(exc),
                    compliance="atencao",
                    duration_seconds=0.0,
                )
            )
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


def choose_step(logger: Logger) -> type[Step] | None:
    options = [MenuOption(str(index), step_cls.title, display_key=f"{index:02d}") for index, step_cls in enumerate(ALL_STEPS, 1)]
    clear_screen()
    try:
        index = choose_option(
            title="Escolha a etapa que voce quer abrir",
            logger=logger,
            prompt="Digite o numero da etapa",
            options=options,
            detail="O sisteminha esta aguardando sua escolha de etapa.",
            prompt_label="Etapa",
        )
    except PromptInterruptedError as exc:
        logger.write(f"{badge('aviso', Color.WARNING)} {exc}")
        return None
    except TuiDependencyError as exc:
        logger.write(f"{badge('erro', Color.ERROR)} {exc}")
        return None
    return ALL_STEPS[index]


def step_menu(step_cls: type[Step], logger: Logger) -> None:
    options = [
        MenuOption("1", "Apply"),
        MenuOption("2", "Dry-run"),
        MenuOption("3", "Status"),
        MenuOption("4", "Undo"),
        MenuOption("5", "Sair"),
    ]
    while True:
        clear_screen()
        try:
            option = choose_option(
                title=f"Etapa {step_cls.id} - {step_cls.title}",
                logger=logger,
                prompt="Escolha uma acao para esta etapa",
                options=options,
                footer="Durante comandos longos, o sisteminha mostra atividade viva para voce saber que nao travou.",
                detail="O sisteminha esta aguardando sua escolha.",
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
            run_action_safe(step_cls, "dry-run", logger)
        elif option == 2:
            run_action_safe(step_cls, "status", logger)
        elif option == 3:
            run_action_safe(step_cls, "undo", logger)
        elif option == 4:
            return


def main_menu(logger: Logger) -> None:
    options = [
        MenuOption("1", "Apply completo"),
        MenuOption("2", "Dry-run completo"),
        MenuOption("3", "Status completo"),
        MenuOption("4", "Apply por etapa"),
        MenuOption("5", "Dry-run por etapa"),
        MenuOption("6", "Undo por etapa"),
        MenuOption("7", "Sair"),
    ]
    while True:
        clear_screen()
        try:
            option = choose_option(
                title="Sisteminha pos-formatacao CachyOS/KDE",
                logger=logger,
                prompt="Escolha uma opcao do menu principal",
                options=options,
                footer="Tema neon ativo quando o terminal suporta ANSI. Use NO_COLOR=1 para desativar as cores.",
                detail="Quando o menu esta aqui, o sisteminha esta esperando voce e nao travado.",
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
            run_all("dry-run", logger)
        elif option == 2:
            run_all("status", logger)
        elif option == 3:
            step = choose_step(logger)
            if step:
                run_action_safe(step, "apply", logger)
        elif option == 4:
            step = choose_step(logger)
            if step:
                run_action_safe(step, "dry-run", logger)
        elif option == 5:
            step = choose_step(logger)
            if step:
                run_action_safe(step, "undo", logger)
        elif option == 6:
            return


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    logger = Logger(Path.cwd(), "00-pos-formatacao-cachyos")
    if is_root():
        logger.write(f"{badge('erro', Color.ERROR)} nao execute como root. Use usuario normal; sudo sera chamado quando necessario.")
        return 1
    if no_new_privs_enabled():
        logger.write(f"{badge('aviso', Color.WARNING)} este terminal bloqueia sudo (NoNewPrivs=1).")
        logger.write("Status e dry-run continuam funcionando, mas Apply de etapas privilegiadas precisa ser executado em uma sessao normal do sistema.")
    if argv and argv[0] == "step":
        if len(argv) < 2:
            logger.write("Uso: python -m postformat.cli step ID [apply|dry-run|status|undo|menu]")
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
        except (PrivilegeEscalationBlockedError, CommandInterruptedError, PromptInterruptedError, TuiDependencyError) as exc:
            logger.write(f"{badge('erro', Color.ERROR)} {exc}")
            return 1
        return 0
    main_menu(logger)
    return 0


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
    if action == "status":
        if result.compliance == "aplicado":
            return result.summary or "Etapa aplicada."
        if result.compliance == "pendente":
            return result.summary or "Ha itens pendentes nesta etapa."
        if result.compliance == "atencao":
            return result.summary or "A etapa requer atencao."
        return "Status coletado."
    if action == "dry-run":
        return result.summary or "Dry-run concluido."
    if action == "undo":
        return result.summary or "Undo concluido."
    return result.summary or result.message or "Etapa concluida."


def render_step_summary(logger: Logger, action: str, result: StepRunResult) -> None:
    tone = {
        "done": Color.SUCCESS,
        "skipped": Color.WARNING,
        "manual": Color.WARNING,
        "failed": Color.ERROR,
        "blocked": Color.ERROR,
    }.get(result.status, Color.INFO)
    logger.write("")
    logger.write(divider(char="#", tone=Color.TITLE))
    logger.write(paint("Resumo da etapa", Color.TITLE))
    logger.write(paint(f"Modo: {action}  |  Etapa: [{result.step_id}] {result.title}  |  Duracao: {format_elapsed(result.duration_seconds)}", Color.MUTED))
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
    for item, compliance in classified:
        tone = {"aplicado": Color.SUCCESS, "pendente": Color.WARNING, "atencao": Color.ERROR}[compliance]
        logger.write(f"{badge(compliance, tone)} [{item.step_id}] {item.title}")
        logger.write(paint(item.message, Color.MUTED))
    logger.write(divider(char="#", tone=Color.TITLE))


def prompt_return_to_menu(logger: Logger) -> None:
    try:
        prompt_user(
            "Pressione ENTER para voltar ao menu",
            logger,
            detail="O sisteminha esta pausado para voce conseguir ler o resumo.",
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
    logger.write(paint(f"Modo: {action}  |  Duracao total: {format_elapsed(duration_seconds)}  |  Log: {logger.path}", Color.MUTED))
    logger.write(divider(char="-", tone=Color.BOX))
    logger.write(f"{badge('done', Color.SUCCESS)} {counts['done']} concluida(s)")
    logger.write(f"{badge('skipped', Color.WARNING)} {counts['skipped']} pulada(s)")
    logger.write(f"{badge('manual', Color.WARNING)} {counts['manual']} com interacao manual")
    logger.write(f"{badge('failed', Color.ERROR)} {counts['failed']} falha(s)")
    logger.write(f"{badge('blocked', Color.ERROR)} {counts['blocked']} bloqueada(s)")
    logger.write(paint(f"Executadas: {len(results)}/{total_steps}", Color.ACCENT))
    logger.write(divider(char="-", tone=Color.BOX))
    for item in results:
        tone = {
            "done": Color.SUCCESS,
            "skipped": Color.WARNING,
            "manual": Color.WARNING,
            "failed": Color.ERROR,
            "blocked": Color.ERROR,
        }.get(item.status, Color.INFO)
        logger.write(
            f"{badge(item.status, tone)} [{item.step_id}] {item.title}  "
            f"{paint(f'({format_elapsed(item.duration_seconds)})', Color.MUTED)}"
        )
        if item.message:
            logger.write(paint(item.message, Color.MUTED))
    logger.write(divider(char="#", tone=Color.TITLE))


if __name__ == "__main__":
    raise SystemExit(main())
