from pathlib import Path

from postformat.core import Logger, Runner, backup_path
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
    )

    rendered = entry.render()

    assert "Name=Hydra Launcher" in rendered
    assert "Exec=/home/user/AppImages/Hydra.AppImage %U" in rendered
    assert "Icon=/home/user/.local/share/icons/hydra.png" in rendered
    assert "Categories=Game;" in rendered
