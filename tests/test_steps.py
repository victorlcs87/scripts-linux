from pathlib import Path

from postformat.cli import render_run_summary
from postformat.core import Logger, Runner, StepRunResult, UserInfo
from postformat.steps import ALL_STEPS, AppsStep, NumLockStep, ShellyStep
from postformat.steps_base import StepContext


def make_ctx(tmp_path: Path) -> StepContext:
    user_home = tmp_path / "home"
    user_home.mkdir()
    user = UserInfo(name="tester", home=user_home, uid=1000, gid=1000)
    logger = Logger(tmp_path, "test")
    return StepContext(
        root=Path.cwd(),
        run_dir=tmp_path,
        user=user,
        logger=logger,
        runner=Runner(logger, dry_run=True),
    )


def test_numlock_ini_value_is_updated(tmp_path: Path) -> None:
    step = NumLockStep(make_ctx(tmp_path))
    text = "[Keyboard]\nNumLock=2\nRepeatDelay=600\n"

    updated = step._set_ini_value(text, "Keyboard", "NumLock", "0")

    assert "NumLock=0" in updated
    assert "NumLock=2" not in updated
    assert "RepeatDelay=600" in updated


def test_all_steps_use_sequential_ids() -> None:
    ids = [step.id for step in ALL_STEPS]

    assert ids == [f"{index:02d}" for index in range(len(ALL_STEPS))]
    assert all("." not in step_id for step_id in ids)


def test_shelly_step_dry_run_prepares_stack_without_ui_when_ready(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = ShellyStep(ctx)

    monkeypatch.setattr("postformat.steps.command_exists", lambda name: name in {"flatpak", "shelly"})
    monkeypatch.setattr("postformat.steps.aur_helper", lambda: "paru")
    monkeypatch.setattr("postformat.steps.pacman_installed", lambda pkg: pkg == "fuse2")

    def fake_run(cmd, **kwargs):
        if cmd == ["flatpak", "remote-list", "--columns=name"]:
            class Result:
                stdout = "flathub\n"
                returncode = 0
            return Result()
        return None

    monkeypatch.setattr(ctx.runner, "run", fake_run)

    step.apply()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert "Ecossistema" in log or "ja estavam prontos" in log
    assert "abriria Shelly" not in log


def test_apps_dry_run_mentions_appimage_and_codex(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    step = AppsStep(ctx)

    step.apply()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert "fuse2" in log
    assert "@openai/codex" in log
    assert "com.discordapp.Discord" in log


def test_render_run_summary_aggregates_counts(tmp_path: Path) -> None:
    logger = Logger(tmp_path, "test")
    results = [
        StepRunResult("00", "Preparar", "done", "ok", 1.2),
        StepRunResult("01", "Atualizar", "skipped", "skip", 0.1),
        StepRunResult("02", "Linux Toys", "manual", "manual", 0.3),
        StepRunResult("03", "Browser", "failed", "fail", 0.2),
    ]

    render_run_summary(logger, "apply", results, 13, 4.8)
    log = logger.path.read_text(encoding="utf-8")

    assert "Resumo final do fluxo" in log
    assert "[done] 1" in log
    assert "[skipped] 1" in log
    assert "[manual] 1" in log
    assert "[failed] 1" in log
