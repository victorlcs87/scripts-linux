from __future__ import annotations

import sys
import time
from pathlib import Path

from .core import (
    Color,
    CommandInterruptedError,
    Logger,
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
    announce,
)
from .steps import ALL_STEPS
from .steps_base import Step, StepContext


ROOT = Path(__file__).resolve().parent.parent


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
    return StepRunResult(
        step_id=step_cls.id,
        title=step_cls.title,
        status=step.result.status,
        message=step.result.message,
        duration_seconds=time.monotonic() - started,
    )


def run_all(action: str, logger: Logger) -> None:
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
                    duration_seconds=0.0,
                )
            )
            if action in {"apply", "dry-run"}:
                prompt_user(
                    "Pressione ENTER para continuar com a proxima etapa ou Ctrl+C para parar",
                    logger,
                    detail="O fluxo esta pausado aguardando sua decisao.",
                    prompt_label="ENTER",
                )
    render_run_summary(logger, action, results, total, time.monotonic() - overall_started)


def choose_step(logger: Logger) -> type[Step] | None:
    print(divider(char="~", tone=Color.ACCENT))
    print(paint("Escolha a etapa que voce quer abrir", Color.TITLE))
    for index, step_cls in enumerate(ALL_STEPS, 1):
        print(f"{paint(f'{index:02d}.', Color.CHOICE)} {badge(step_cls.id, Color.ACCENT)} {paint(step_cls.title, Color.INFO)}")
    choice = prompt_user(
        "Digite o numero da etapa",
        logger,
        detail="O sisteminha esta aguardando sua escolha de etapa.",
        prompt_label="Etapa",
    ).strip()
    if not choice.isdigit():
        print(paint("Opcao invalida", Color.ERROR))
        return None
    index = int(choice)
    if index < 1 or index > len(ALL_STEPS):
        print(paint("Opcao invalida", Color.ERROR))
        return None
    return ALL_STEPS[index - 1]


def render_menu(title: str, logger: Logger, items: list[str], *, footer: str | None = None) -> str:
    body = [
        divider(char="#", tone=Color.TITLE),
        paint(title, Color.TITLE),
        paint("Visual impactante ativo  |  estados vivos  |  prompts explicitamente sinalizados", Color.ACCENT),
        paint(f"Log: {logger.path}", Color.MUTED),
        divider(char="-", tone=Color.BOX),
    ]
    body.extend(items)
    if footer:
        body.extend([divider(char="-", tone=Color.BOX), paint(footer, Color.MUTED)])
    body.append(divider())
    return "\n".join(body)


def step_menu(step_cls: type[Step], logger: Logger) -> None:
    while True:
        print(
            "\n"
            + render_menu(
                f"Etapa {step_cls.id} - {step_cls.title}",
                logger,
                [
                    f"{paint('1.', Color.CHOICE)} {paint('Apply', Color.SUCCESS)}",
                    f"{paint('2.', Color.CHOICE)} {paint('Dry-run', Color.DRY_RUN)}",
                    f"{paint('3.', Color.CHOICE)} {paint('Status', Color.INFO)}",
                    f"{paint('4.', Color.CHOICE)} {paint('Undo', Color.WARNING)}",
                    f"{paint('5.', Color.CHOICE)} {paint('Sair', Color.MUTED)}",
                ],
                footer="Durante comandos longos, o sisteminha mostra atividade viva para voce saber que nao travou.",
            )
        )
        option = prompt_user(
            "Escolha uma acao para esta etapa",
            logger,
            detail="O sisteminha esta aguardando sua escolha.",
            prompt_label="Escolha",
        ).strip()
        if option == "1":
            run_action(step_cls, "apply", logger)
        elif option == "2":
            run_action(step_cls, "dry-run", logger)
        elif option == "3":
            run_action(step_cls, "status", logger)
        elif option == "4":
            run_action(step_cls, "undo", logger)
        elif option == "5":
            return
        else:
            print(paint("Opcao invalida", Color.ERROR))


def main_menu(logger: Logger) -> None:
    while True:
        print(
            "\n"
            + render_menu(
                "Sisteminha pos-formatacao CachyOS/KDE",
                logger,
                [
                    f"{paint('1.', Color.CHOICE)} {paint('Apply completo', Color.SUCCESS)}",
                    f"{paint('2.', Color.CHOICE)} {paint('Dry-run completo', Color.DRY_RUN)}",
                    f"{paint('3.', Color.CHOICE)} {paint('Status completo', Color.INFO)}",
                    f"{paint('4.', Color.CHOICE)} {paint('Apply por etapa', Color.ACCENT)}",
                    f"{paint('5.', Color.CHOICE)} {paint('Dry-run por etapa', Color.ACCENT)}",
                    f"{paint('6.', Color.CHOICE)} {paint('Undo por etapa', Color.WARNING)}",
                    f"{paint('7.', Color.CHOICE)} {paint('Sair', Color.MUTED)}",
                ],
                footer="Tema neon ativo quando o terminal suporta ANSI. Use NO_COLOR=1 para desativar as cores.",
            )
        )
        option = prompt_user(
            "Escolha uma opcao do menu principal",
            logger,
            detail="Quando o menu esta aqui, o sisteminha esta esperando voce e nao travado.",
            prompt_label="Escolha",
        ).strip()
        if option == "1":
            run_all("apply", logger)
        elif option == "2":
            run_all("dry-run", logger)
        elif option == "3":
            run_all("status", logger)
        elif option == "4":
            step = choose_step(logger)
            if step:
                run_action(step, "apply", logger)
        elif option == "5":
            step = choose_step(logger)
            if step:
                run_action(step, "dry-run", logger)
        elif option == "6":
            step = choose_step(logger)
            if step:
                run_action(step, "undo", logger)
        elif option == "7":
            return
        else:
            print(paint("Opcao invalida", Color.ERROR))


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
        except (PrivilegeEscalationBlockedError, CommandInterruptedError) as exc:
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
