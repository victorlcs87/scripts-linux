"""Testes da camada de I/O abstraida (Fase 1/2): provider de interacao,
askpass do Runner e executor interactive_tty plugavel. Nao dependem de PySide6.
"""

from __future__ import annotations

from pathlib import Path

from reforja.core import Logger, Runner, clean_subprocess_env, confirm_phrase, prompt_user, select_many


class FakeInteraction:
    def __init__(self, *, text: str = "", confirm: bool = True, choices: list[int] | None = None) -> None:
        self.text = text
        self.confirm = confirm
        self.choices = choices if choices is not None else []
        self.text_calls: list[str] = []
        self.confirm_calls: list[str] = []
        self.choose_calls: list[tuple[str, list[str]]] = []
        self.preselected_calls: list[list[int]] = []

    def ask_text(self, prompt, *, detail=None, prompt_label="Resposta", allow_empty=True) -> str:
        self.text_calls.append(prompt)
        return self.text

    def confirm_phrase(self, phrase, *, detail=None) -> bool:
        self.confirm_calls.append(phrase)
        return self.confirm

    def choose_many(self, prompt, options, *, detail=None, preselected=()) -> list[int]:
        self.choose_calls.append((prompt, list(options)))
        self.preselected_calls.append(list(preselected))
        return self.choices


def test_prompt_user_delega_para_interaction(tmp_path: Path) -> None:
    logger = Logger(tmp_path, "test")
    logger.interaction = FakeInteraction(text="resposta-x")
    answer = prompt_user("Qual o valor?", logger, prompt_label="Valor")
    assert answer == "resposta-x"
    assert logger.interaction.text_calls == ["Qual o valor?"]


def test_confirm_phrase_delega_para_interaction(tmp_path: Path) -> None:
    logger = Logger(tmp_path, "test")
    logger.interaction = FakeInteraction(confirm=True)
    assert confirm_phrase("APLICAR-FSTAB", logger) is True
    logger.interaction.confirm = False
    assert confirm_phrase("APLICAR-FSTAB", logger) is False


def test_select_many_delega_para_interaction(tmp_path: Path) -> None:
    logger = Logger(tmp_path, "test")
    logger.interaction = FakeInteraction(choices=[0, 2])
    indices = select_many("Quais itens", ["a", "b", "c"], logger)
    assert indices == [0, 2]
    assert logger.interaction.choose_calls == [("Quais itens", ["a", "b", "c"])]


def test_select_many_vazio_retorna_lista_vazia(tmp_path: Path) -> None:
    logger = Logger(tmp_path, "test")
    logger.interaction = FakeInteraction(choices=[])
    assert select_many("Quais itens", [], logger) == []


def test_select_many_fallback_usa_tui_quando_sem_interaction(tmp_path: Path, monkeypatch) -> None:
    logger = Logger(tmp_path, "test")  # interaction=None -> fallback do terminal
    capturado: dict[str, object] = {}

    def fake_choose_multiple(*, title, logger, prompt, options, detail=None, footer=None, preselected=()):
        capturado["labels"] = [option.label for option in options]
        capturado["preselected"] = list(preselected)
        return [1]

    monkeypatch.setattr("reforja.tui.choose_multiple", fake_choose_multiple)
    indices = select_many("Quais itens", ["a", "b", "c"], logger)
    assert indices == [1]
    assert capturado["labels"] == ["a", "b", "c"]
    assert capturado["preselected"] == []


def test_select_many_repassa_preselected_e_descarta_indices_invalidos(tmp_path: Path) -> None:
    logger = Logger(tmp_path, "test")
    logger.interaction = FakeInteraction(choices=[0])
    select_many("Quais itens", ["a", "b", "c"], logger, preselected=[1, 9, -1])
    assert logger.interaction.preselected_calls == [[1]]


def test_runner_askpass_usa_sudo_dash_a(tmp_path: Path) -> None:
    logger = Logger(tmp_path, "test")
    runner = Runner(logger, dry_run=True)
    runner.askpass = "/usr/bin/ksshaskpass"
    # Em dry-run o comando e apenas exibido; conferimos o texto exibido com sudo.
    text = runner.cmd_text(["pacman", "-Syu"], sudo=True)
    assert text == "sudo pacman -Syu"


def test_interactive_executor_e_chamado(tmp_path: Path) -> None:
    logger = Logger(tmp_path, "test")
    runner = Runner(logger, dry_run=False)
    chamadas: list[object] = []

    def fake_executor(cmd, *, cwd, env, action) -> int:
        chamadas.append(cmd)
        return 0

    runner.interactive_executor = fake_executor
    result = runner.run(["echo", "oi"], interactive_tty=True, action="teste")
    assert result is not None
    assert result.returncode == 0
    assert chamadas == [["echo", "oi"]]


def test_clean_subprocess_env_restaura_ld_library_path(monkeypatch) -> None:
    # PyInstaller salva o valor original em LD_LIBRARY_PATH_ORIG.
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bundle/libs")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/lib")
    env = clean_subprocess_env()
    assert env["LD_LIBRARY_PATH"] == "/usr/lib"


def test_clean_subprocess_env_noop_quando_nao_congelado(monkeypatch) -> None:
    # Sem _ORIG e sem sys.frozen: nao altera nada (execucao normal do fonte).
    monkeypatch.setenv("LD_LIBRARY_PATH", "/qualquer")
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    env = clean_subprocess_env()
    assert env["LD_LIBRARY_PATH"] == "/qualquer"
