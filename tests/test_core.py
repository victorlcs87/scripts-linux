from pathlib import Path

import pytest

from postformat.core import (
    Logger,
    PrivilegeEscalationBlockedError,
    PromptInterruptedError,
    Runner,
    backup_path,
    load_env_file,
    progress_bar,
    prompt_user,
    write_text,
)
from postformat.desktop import DesktopEntry


def test_dry_run_does_not_execute_command(tmp_path: Path) -> None:
    logger = Logger(tmp_path, "test")
    runner = Runner(logger, dry_run=True)

    runner.run(["touch", str(tmp_path / "created")])

    assert not (tmp_path / "created").exists()
    assert "[dry-run]" in logger.path.read_text(encoding="utf-8")


def test_backup_path_keeps_original_name() -> None:
    target = backup_path(Path("/tmp/example.conf"))

    assert target.name.startswith("example.conf.backup-pos-formatacao-")


def test_desktop_entry_render() -> None:
    entry = DesktopEntry(
        name="Hydra Launcher",
        exec_line="/home/user/AppImages/Hydra.AppImage %U",
        icon="/home/user/.local/share/icons/hydra.png",
        categories=("Game",),
        startup_wm_class="hydralauncher",
    )

    rendered = entry.render()

    assert "Name=Hydra Launcher" in rendered
    assert "Exec=/home/user/AppImages/Hydra.AppImage %U" in rendered
    assert "Icon=/home/user/.local/share/icons/hydra.png" in rendered
    assert "StartupWMClass=hydralauncher" in rendered
    assert "Categories=Game;" in rendered


def test_write_text_skips_when_content_is_current(tmp_path: Path) -> None:
    logger = Logger(tmp_path, "test")
    runner = Runner(logger, dry_run=False)
    target = tmp_path / "sample.txt"
    target.write_text("same\n", encoding="utf-8")
    before = target.stat().st_mtime_ns

    write_text(target, "same\n", runner)

    assert target.stat().st_mtime_ns == before
    assert "ja esta atualizado" in logger.path.read_text(encoding="utf-8")


def test_sudo_is_blocked_cleanly_when_no_new_privs(monkeypatch, tmp_path: Path) -> None:
    logger = Logger(tmp_path, "test")
    runner = Runner(logger, dry_run=False)
    monkeypatch.setattr("postformat.core.no_new_privs_enabled", lambda: True)

    with pytest.raises(PrivilegeEscalationBlockedError):
        runner.run(["true"], sudo=True)


def test_runner_logs_human_action(tmp_path: Path) -> None:
    logger = Logger(tmp_path, "test")
    runner = Runner(logger, dry_run=False)

    runner.run(["sh", "-c", "printf 'ok\\n'"], action="Executando teste de acao", show_progress=False)
    log = logger.path.read_text(encoding="utf-8")

    assert "[action]" in log
    assert "Executando teste de acao" in log
    assert "[done]" in log


def test_prompt_user_logs_waiting(monkeypatch, tmp_path: Path) -> None:
    logger = Logger(tmp_path, "test")
    monkeypatch.setattr("builtins.input", lambda _prompt="": "resposta")

    answer = prompt_user("Informe algo", logger, detail="aguardando", prompt_label="Campo")
    log = logger.path.read_text(encoding="utf-8")

    assert answer == "resposta"
    assert "[waiting]" in log
    assert "Informe algo" in log


def test_progress_bar_has_percentage() -> None:
    rendered = progress_bar(4, 13)

    assert "30%" in rendered
    assert "[" in rendered and "]" in rendered


def test_runner_marks_interactive_commands(tmp_path: Path) -> None:
    logger = Logger(tmp_path, "test")
    runner = Runner(logger, dry_run=True)

    runner.run(["rclone", "config"], interactive=True, manual_message="interativo")
    log = logger.path.read_text(encoding="utf-8")

    assert "[manual]" in log
    assert "interativo" in log


def test_load_env_file_reads_simple_key_value_pairs(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.local"
    env_file.write_text("ID_DO_CLIENTE = abc\nCHAVE_SECRETA_DO_CLIENTE = xyz\n", encoding="utf-8")

    loaded = load_env_file(env_file)

    assert loaded["ID_DO_CLIENTE"] == "abc"
    assert loaded["CHAVE_SECRETA_DO_CLIENTE"] == "xyz"


def test_prompt_user_handles_ctrl_c_cleanly(monkeypatch, tmp_path: Path) -> None:
    logger = Logger(tmp_path, "test")

    def raise_interrupt(_prompt: str = "") -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", raise_interrupt)

    with pytest.raises(PromptInterruptedError):
        prompt_user("Informe algo", logger, prompt_label="Campo")

    log = logger.path.read_text(encoding="utf-8")
    assert "[skipped]" in log
    assert "Campo interrompido pelo usuario" in log
