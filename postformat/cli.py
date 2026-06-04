from __future__ import annotations

import sys
from pathlib import Path

from .core import Color, Logger, Runner, badge, detect_user, divider, is_root, paint
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


def run_action(step_cls: type[Step], action: str, logger: Logger) -> None:
    dry = action == "dry-run"
    step = build_step(step_cls, logger, dry_run=dry)
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


def run_all(action: str, logger: Logger) -> None:
    for step_cls in ALL_STEPS:
        logger.write("")
        logger.write(divider())
        logger.write(f"{badge(step_cls.id, Color.TITLE)} {paint(step_cls.title, Color.TITLE)}")
        logger.write(divider())
        try:
            run_action(step_cls, action, logger)
        except Exception as exc:
            logger.write(f"{badge('erro', Color.ERROR)} etapa falhou: {exc}")
            if action in {"apply", "dry-run"}:
                input("Pressione ENTER para continuar com a proxima etapa ou Ctrl+C para parar...")


def choose_step() -> type[Step] | None:
    for index, step_cls in enumerate(ALL_STEPS, 1):
        print(f"{paint(f'{index:02d}.', Color.CHOICE)} {badge(step_cls.id, Color.ACCENT)} {paint(step_cls.title, Color.INFO)}")
    choice = input("Etapa: ").strip()
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
        divider(),
        paint(title, Color.TITLE),
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
            )
        )
        option = input("Escolha: ").strip()
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
        option = input("Escolha: ").strip()
        if option == "1":
            run_all("apply", logger)
        elif option == "2":
            run_all("dry-run", logger)
        elif option == "3":
            run_all("status", logger)
        elif option == "4":
            step = choose_step()
            if step:
                run_action(step, "apply", logger)
        elif option == "5":
            step = choose_step()
            if step:
                run_action(step, "dry-run", logger)
        elif option == "6":
            step = choose_step()
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
    if argv and argv[0] == "step":
        if len(argv) < 2:
            logger.write("Uso: python -m postformat.cli step ID [apply|dry-run|status|undo|menu]")
            return 1
        step_cls = step_by_id(argv[1])
        if not step_cls:
            logger.write(f"Etapa nao encontrada: {argv[1]}")
            return 1
        action = argv[2] if len(argv) > 2 else "menu"
        if action == "menu":
            step_menu(step_cls, logger)
        else:
            run_action(step_cls, action, logger)
        return 0
    main_menu(logger)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
