from pathlib import Path

from postformat.core import Logger, Runner, UserInfo
from postformat.steps import AppsStep, NumLockStep
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


def test_apps_dry_run_mentions_appimage_and_codex(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    step = AppsStep(ctx)

    step.apply()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert "fuse2" in log
    assert "@openai/codex" in log
    assert "com.discordapp.Discord" in log
