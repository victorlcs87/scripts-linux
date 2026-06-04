from __future__ import annotations

import shutil
from pathlib import Path

from .core import Color, Runner, announce, command_exists


def pacman_installed(pkg: str) -> bool:
    return shutil.which("pacman") is not None and _quiet(["pacman", "-Q", pkg])


def pacman_exists(pkg: str) -> bool:
    return shutil.which("pacman") is not None and _quiet(["pacman", "-Si", pkg])


def _quiet(cmd: list[str]) -> bool:
    import subprocess

    return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode == 0


def aur_helper() -> str | None:
    for candidate in ("paru", "yay"):
        if command_exists(candidate):
            return candidate
    return None


def install_pacman(pkg: str, runner: Runner) -> None:
    if pacman_installed(pkg):
        announce(runner.logger, "skipped", f"{pkg} ja instalado")
        return
    runner.run(["pacman", "-S", "--needed", pkg], sudo=True, action=f"Instalando pacote {pkg}")


def install_system_or_aur(system_pkg: str, aur_pkg: str | None, runner: Runner) -> bool:
    if pacman_installed(system_pkg):
        announce(runner.logger, "skipped", f"{system_pkg} ja instalado")
        return True
    if aur_pkg and pacman_installed(aur_pkg):
        announce(runner.logger, "skipped", f"{aur_pkg} ja instalado")
        return True
    if pacman_exists(system_pkg):
        install_pacman(system_pkg, runner)
        return True
    helper = aur_helper()
    if aur_pkg and helper:
        runner.run([helper, "-S", "--needed", aur_pkg], action=f"Instalando pacote AUR {aur_pkg}")
        return True
    runner.logger.write(f"{Color.YELLOW}AVISO:{Color.RESET} nao encontrei pacote para {system_pkg}")
    return False


def ensure_flatpak(runner: Runner) -> None:
    if not command_exists("flatpak"):
        install_pacman("flatpak", runner)
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
