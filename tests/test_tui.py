from pathlib import Path

import pytest

from postformat.core import Logger, MenuOption, PromptInterruptedError
from postformat.tui import TuiDependencyError, choose_option


class _FakePrompt:
    def __init__(self, value):
        self._value = value

    def execute(self):
        return self._value


class _FakeInquirer:
    def __init__(self, value):
        self._value = value
        self.calls = []

    def select(self, **kwargs):
        self.calls.append(kwargs)
        return _FakePrompt(self._value)


def test_choose_option_uses_inquirer_in_tty(monkeypatch, tmp_path: Path) -> None:
    logger = Logger(tmp_path, "test")
    fake_inquirer = _FakeInquirer(1)
    options = [MenuOption("1", "Primeira"), MenuOption("2", "Segunda")]
    fake_style_factory = lambda style, style_override=False: {"style": style, "style_override": style_override}

    monkeypatch.setattr("postformat.tui._supports_interactive_tui", lambda: True)
    monkeypatch.setattr("postformat.tui.load_tui_deps", lambda: (fake_inquirer, fake_style_factory))

    selected = choose_option(
        title="Menu Teste",
        logger=logger,
        prompt="Escolha algo",
        options=options,
        footer="Rodape",
    )

    assert selected == 1
    assert fake_inquirer.calls[0]["message"] == "Escolha algo"
    assert fake_inquirer.calls[0]["choices"][1]["name"] == "2. Segunda"
    assert fake_inquirer.calls[0]["instruction"] == "setas navegam | Enter confirma | numero visivel na lista"
    assert "pointer" in fake_inquirer.calls[0]["style"]["style"]


def test_choose_option_fallback_accepts_number(monkeypatch, tmp_path: Path) -> None:
    logger = Logger(tmp_path, "test")
    options = [MenuOption("1", "Primeira"), MenuOption("2", "Segunda")]

    monkeypatch.setattr("postformat.tui._supports_interactive_tui", lambda: False)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "2")

    selected = choose_option(
        title="Menu Teste",
        logger=logger,
        prompt="Escolha algo",
        options=options,
        prompt_label="Opcao",
    )

    assert selected == 1


def test_choose_option_fallback_retries_after_invalid_number(monkeypatch, tmp_path: Path, capsys) -> None:
    logger = Logger(tmp_path, "test")
    options = [MenuOption("1", "Primeira"), MenuOption("2", "Segunda")]
    answers = iter(["9", "2"])

    monkeypatch.setattr("postformat.tui._supports_interactive_tui", lambda: False)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))

    selected = choose_option(
        title="Menu Teste",
        logger=logger,
        prompt="Escolha algo",
        options=options,
        prompt_label="Opcao",
    )

    assert selected == 1
    assert "Opcao invalida" in capsys.readouterr().out


def test_choose_option_tty_interrupt_is_reported_cleanly(monkeypatch, tmp_path: Path) -> None:
    logger = Logger(tmp_path, "test")
    options = [MenuOption("1", "Primeira"), MenuOption("2", "Segunda")]
    class _InterruptingInquirer:
        def select(self, **_kwargs):
            class _InterruptingPrompt:
                def execute(self):
                    raise KeyboardInterrupt

            return _InterruptingPrompt()

    monkeypatch.setattr("postformat.tui._supports_interactive_tui", lambda: True)
    monkeypatch.setattr("postformat.tui.load_tui_deps", lambda: (_InterruptingInquirer(), lambda style, style_override=False: style))

    with pytest.raises(PromptInterruptedError):
        choose_option(
            title="Menu Teste",
            logger=logger,
            prompt="Escolha algo",
            options=options,
            prompt_label="Opcao",
        )


def test_choose_option_raises_clear_dependency_error(monkeypatch, tmp_path: Path) -> None:
    logger = Logger(tmp_path, "test")
    options = [MenuOption("1", "Primeira")]

    monkeypatch.setattr("postformat.tui._supports_interactive_tui", lambda: True)
    monkeypatch.setattr("postformat.tui.load_tui_deps", lambda: (_ for _ in ()).throw(TuiDependencyError("faltou lib")))

    with pytest.raises(TuiDependencyError):
        choose_option(
            title="Menu Teste",
            logger=logger,
            prompt="Escolha algo",
            options=options,
        )
