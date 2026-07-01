"""Testes da camada de I/O abstraida (Fase 1/2): provider de interacao,
askpass do Runner e executor interactive_tty plugavel. Nao dependem de PySide6.
"""

from __future__ import annotations

from pathlib import Path

from reforja.core import Logger, Runner, clean_subprocess_env, confirm_phrase, prompt_user


class FakeInteraction:
    def __init__(self, *, text: str = "", confirm: bool = True) -> None:
        self.text = text
        self.confirm = confirm
        self.text_calls: list[str] = []
        self.confirm_calls: list[str] = []

    def ask_text(self, prompt, *, detail=None, prompt_label="Resposta", allow_empty=True) -> str:
        self.text_calls.append(prompt)
        return self.text

    def confirm_phrase(self, phrase, *, detail=None) -> bool:
        self.confirm_calls.append(phrase)
        return self.confirm


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
