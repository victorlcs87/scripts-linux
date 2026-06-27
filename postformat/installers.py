from __future__ import annotations

import shutil
from pathlib import Path

from .core import Color, Runner, announce, command_exists
from .platform import (
    aur_helper,
    ensure_rpmfusion,
    install_first_available,
    install_system_or_aur,
    install_system_package,
    system_installed,
    system_package_exists,
)


def pacman_installed(pkg: str) -> bool:
    return system_installed(pkg)


def pacman_exists(pkg: str) -> bool:
    return system_package_exists(pkg)


def _quiet(cmd: list[str]) -> bool:
    import subprocess

    return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode == 0


def install_pacman(pkg: str, runner: Runner) -> None:
    install_system_package(pkg, runner)


def ensure_flatpak(runner: Runner) -> None:
    if not command_exists("flatpak"):
        install_system_package("flatpak", runner)
    runner.run(
        ["flatpak", "remote-add", "--if-not-exists", "flathub", "https://flathub.org/repo/flathub.flatpakrepo"],
        check=False,
        action="Garantindo remote Flathub",
        show_progress=False,
        quiet_success=True,
    )


def install_flatpak(app_id: str, runner: Runner) -> None:
    ensure_flatpak(runner)
    if flatpak_installed(app_id):
        announce(runner.logger, "skipped", f"{app_id} ja instalado via Flatpak")
        return
    runner.run(["flatpak", "install", "-y", "flathub", app_id], action=f"Instalando Flatpak {app_id}")


def remove_flatpak(app_id: str, runner: Runner) -> None:
    runner.run(["flatpak", "uninstall", "-y", app_id], check=False, action=f"Removendo Flatpak {app_id}")


def copy_asset(source: Path, target: Path, runner: Runner) -> None:
    if target.exists() and source.exists() and target.read_bytes() == source.read_bytes():
        announce(runner.logger, "skipped", f"{target} ja esta atualizado")
        return
    if runner.dry_run:
        runner.logger.write(f"{Color.YELLOW}[dry-run]{Color.RESET} cp {source} {target}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def flatpak_installed(app_id: str) -> bool:
    return shutil.which("flatpak") is not None and _quiet(["flatpak", "info", app_id])


def npm_global_installed(pkg: str) -> bool:
    if shutil.which("npm") is None:
        return False
    return _quiet(["npm", "list", "-g", pkg, "--depth=0"])


__all__ = [
    "aur_helper",
    "copy_asset",
    "ensure_flatpak",
    "ensure_rpmfusion",
    "flatpak_installed",
    "install_first_available",
    "install_flatpak",
    "install_pacman",
    "install_system_or_aur",
    "install_system_package",
    "npm_global_installed",
    "pacman_exists",
    "pacman_installed",
    "remove_flatpak",
    "system_installed",
    "system_package_exists",
]
