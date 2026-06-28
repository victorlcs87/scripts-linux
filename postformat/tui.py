from __future__ import annotations

import sys
from collections.abc import Sequence

from .core import Color, Logger, MenuOption, PromptInterruptedError, badge, divider, paint, prompt_user


class TuiDependencyError(RuntimeError):
    pass


def load_tui_deps():
    try:
        from InquirerPy import inquirer  # type: ignore
        from InquirerPy.utils import get_style  # type: ignore
    except ImportError as exc:
        raise TuiDependencyError(
            "InquirerPy nao esta instalado. Execute o script principal "
            "`python 00-pos-formatacao-cachyos.py` para preparar as dependencias internas."
        ) from exc
    return inquirer, get_style


def choose_option(
    *,
    title: str,
    logger: Logger,
    prompt: str,
    options: Sequence[MenuOption],
    footer: str | None = None,
    detail: str | None = None,
    prompt_label: str = "Escolha",
    initial_index: int = 0,
) -> int:
    if not options:
        raise ValueError("lista de opcoes nao pode ser vazia")
    if initial_index < 0 or initial_index >= len(options):
        initial_index = 0
    logger.log_only(divider(char="~", tone=Color.ACCENT))
    logger.log_only(f"{badge('waiting', Color.WAITING)} {prompt}")
    if detail:
        logger.log_only(paint(detail, Color.MUTED))
    if _supports_interactive_tui():
        selected = _choose_option_tty(
            title=title,
            logger=logger,
            prompt=prompt,
            options=options,
            footer=footer,
            detail=detail,
            initial_index=initial_index,
        )
    else:
        selected = _choose_option_fallback(
            title=title,
            logger=logger,
            prompt=prompt,
            options=options,
            footer=footer,
            detail=detail,
            prompt_label=prompt_label,
        )
    logger.log_only(f"{badge('choice', Color.CHOICE)} {options[selected].key} - {options[selected].label}")
    return selected


def render_menu(title: str, logger: Logger, options: Sequence[MenuOption], *, footer: str | None = None) -> str:
    body = _menu_frame(title, logger, footer=footer)
    body.extend(_render_menu_lines(options))
    body.append(divider())
    return "\n".join(body)


def _supports_interactive_tui() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _menu_frame(title: str, logger: Logger, *, footer: str | None = None) -> list[str]:
    body = [
        divider(char="#", tone=Color.TITLE),
        paint(title, Color.TITLE),
        paint("Visual impactante ativo  |  estados vivos  |  prompts explicitamente sinalizados", Color.ACCENT),
        paint(f"Log: {logger.path}", Color.MUTED),
        divider(char="-", tone=Color.BOX),
    ]
    if footer:
        body.extend([paint(footer, Color.MUTED), divider(char="-", tone=Color.BOX)])
    return body


def _render_menu_lines(options: Sequence[MenuOption]) -> list[str]:
    lines: list[str] = []
    for option in options:
        display_key = option.display_key or option.key
        lines.append(f"{paint(f'{display_key}.', Color.CHOICE)} {paint(option.label, Color.INFO)}")
    return lines


def _build_prompt_message(title: str, logger: Logger, *, footer: str | None = None, detail: str | None = None) -> str:
    lines = _menu_frame(title, logger, footer=footer)
    if detail:
        lines.append(paint(detail, Color.MUTED))
    return "\n".join(lines)


def _choose_option_tty(
    *,
    title: str,
    logger: Logger,
    prompt: str,
    options: Sequence[MenuOption],
    footer: str | None,
    detail: str | None,
    initial_index: int,
) -> int:
    inquirer, get_style = load_tui_deps()
    clear_render = _build_prompt_message(title, logger, footer=footer, detail=detail)
    print(clear_render)
    style = get_style(
        {
            "questionmark": "#5fd7ff bold",
            "question": "#ff8fd8 bold",
            "pointer": "#7dff7d bold",
            "instruction": "#87afff",
            "answer": "#7dff7d bold",
            "separator": "#5f87ff",
        },
        style_override=False,
    )
    try:
        selected = inquirer.select(
            message=prompt,
            choices=[
                {
                    "name": f"{option.display_key or option.key}. {option.label}",
                    "value": index,
                }
                for index, option in enumerate(options)
            ],
            default=initial_index,
            instruction="setas navegam | Enter confirma | numero visivel na lista",
            long_instruction="",
            style=style,
            cycle=True,
        ).execute()
    except (KeyboardInterrupt, EOFError) as exc:
        raise PromptInterruptedError(f"entrada interrompida pelo usuario: {title}") from exc
    return int(selected)


def _choose_option_fallback(
    *,
    title: str,
    logger: Logger,
    prompt: str,
    options: Sequence[MenuOption],
    footer: str | None,
    detail: str | None,
    prompt_label: str,
) -> int:
    print(render_menu(title, logger, options, footer=footer))
    while True:
        answer = prompt_user(
            prompt,
            logger,
            detail=detail,
            prompt_label=prompt_label,
            allow_empty=False,
        ).strip()
        for index, option in enumerate(options):
            if answer == option.key:
                return index
        print(paint("Opcao invalida", Color.ERROR))
