from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .core import Color, Runner, announce, command_exists


class UnsupportedDistroError(RuntimeError):
    pass


@dataclass(frozen=True)
class Distro:
    id: str
    id_like: tuple[str, ...]
    family: str

    @property
    def is_arch(self) -> bool:
        return self.family == "arch"

    @property
    def is_debian(self) -> bool:
        return self.family == "debian"


def read_os_release(path: Path = Path("/etc/os-release")) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        raise UnsupportedDistroError("/etc/os-release nao encontrado; nao consegui detectar a distribuicao.")
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def detect_distro(path: Path = Path("/etc/os-release")) -> Distro:
    values = read_os_release(path)
    distro_id = values.get("ID", "").strip().lower()
    id_like = tuple(item.strip().lower() for item in values.get("ID_LIKE", "").split() if item.strip())
    candidates = {distro_id, *id_like}
    if {"arch", "cachyos", "manjaro"} & candidates:
        return Distro(id=distro_id, id_like=id_like, family="arch")
    if {"debian", "ubuntu", "linuxmint", "pop"} & candidates:
        return Distro(id=distro_id, id_like=id_like, family="debian")
    pretty = values.get("PRETTY_NAME") or distro_id or "desconhecida"
    raise UnsupportedDistroError(f"distribuicao nao suportada: {pretty}. Suportadas: Arch/CachyOS e Debian/Ubuntu.")


def current_distro() -> Distro:
    return detect_distro()


def _quiet(cmd: list[str]) -> bool:
    return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode == 0


def system_installed(pkg: str) -> bool:
    distro = current_distro()
    if distro.is_arch:
        return shutil.which("pacman") is not None and _quiet(["pacman", "-Q", pkg])
    if distro.is_debian:
        if shutil.which("dpkg-query") is None:
            return False
        result = subprocess.run(
            ["dpkg-query", "-W", "-f=${Status}", pkg],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        return result.returncode == 0 and "install ok installed" in result.stdout
    return False


def system_package_exists(pkg: str) -> bool:
    distro = current_distro()
    if distro.is_arch:
        return shutil.which("pacman") is not None and _quiet(["pacman", "-Si", pkg])
    if distro.is_debian:
        return shutil.which("apt-cache") is not None and _quiet(["apt-cache", "show", pkg])
    return False


def aur_helper() -> str | None:
    if not current_distro().is_arch:
        return None
    for candidate in ("paru", "yay"):
        if command_exists(candidate):
            return candidate
    return None


def install_system_package(pkg: str, runner: Runner) -> None:
    if system_installed(pkg):
        announce(runner.logger, "skipped", f"{pkg} ja instalado")
        return
    distro = current_distro()
    if distro.is_arch:
        runner.run(
            ["pacman", "-S", "--needed", pkg],
            sudo=True,
            action=f"Instalando pacote {pkg}",
            interactive=True,
            interactive_tty=True,
            manual_message="Comando interativo: o pacman pode pedir senha do sudo e confirmacoes.",
        )
        return
    if distro.is_debian:
        runner.run(
            ["apt-get", "update"],
            sudo=True,
            action="Atualizando indice de pacotes apt",
            interactive=True,
            interactive_tty=True,
            manual_message="Comando interativo: o apt pode pedir senha do sudo.",
        )
        runner.run(
            ["apt-get", "install", "-y", pkg],
            sudo=True,
            action=f"Instalando pacote {pkg}",
            interactive=True,
            interactive_tty=True,
            manual_message="Comando interativo: o apt pode pedir senha do sudo e confirmacoes.",
        )
        return
    raise UnsupportedDistroError(f"familia de distro nao suportada: {distro.family}")


def install_first_available(packages: tuple[str, ...] | list[str], runner: Runner) -> str | None:
    for pkg in packages:
        if system_installed(pkg):
            announce(runner.logger, "skipped", f"{pkg} ja instalado")
            return pkg
    for pkg in packages:
        if system_package_exists(pkg):
            install_system_package(pkg, runner)
            return pkg
    runner.logger.write(f"{Color.YELLOW}AVISO:{Color.RESET} nao encontrei pacote disponivel entre: {', '.join(packages)}")
    return None


def install_system_or_aur(system_pkg: str, aur_pkg: str | None, runner: Runner) -> bool:
    if system_installed(system_pkg):
        announce(runner.logger, "skipped", f"{system_pkg} ja instalado")
        return True
    if aur_pkg and system_installed(aur_pkg):
        announce(runner.logger, "skipped", f"{aur_pkg} ja instalado")
        return True
    if system_package_exists(system_pkg):
        install_system_package(system_pkg, runner)
        return True
    helper = aur_helper()
    if aur_pkg and helper:
        runner.run(
            [helper, "-S", "--needed", aur_pkg],
            action=f"Instalando pacote AUR {aur_pkg}",
            interactive=True,
            interactive_tty=True,
            manual_message="Comando interativo: o helper AUR pode pedir confirmacoes.",
        )
        return True
    runner.logger.write(f"{Color.YELLOW}AVISO:{Color.RESET} nao encontrei pacote para {system_pkg}")
    return False


def update_system(runner: Runner) -> None:
    distro = current_distro()
    if distro.is_arch:
        install_system_package("pacman-contrib", runner)
        runner.run(
            ["pacman", "-Syu"],
            sudo=True,
            action="Atualizando sistema com pacman",
            interactive=True,
            interactive_tty=True,
            manual_message="Comando interativo: o pacman pode pedir senha do sudo e confirmacoes. Isso nao e travamento.",
        )
        return
    if distro.is_debian:
        runner.run(
            ["apt-get", "update"],
            sudo=True,
            action="Atualizando indice de pacotes apt",
            interactive=True,
            interactive_tty=True,
            manual_message="Comando interativo: o apt pode pedir senha do sudo.",
        )
        runner.run(
            ["apt-get", "upgrade", "-y"],
            sudo=True,
            action="Atualizando sistema com apt",
            interactive=True,
            interactive_tty=True,
            manual_message="Comando interativo: o apt pode pedir senha do sudo e confirmacoes. Isso nao e travamento.",
        )
        return
    raise UnsupportedDistroError(f"familia de distro nao suportada: {distro.family}")


def pending_updates_command() -> list[str] | str:
    distro = current_distro()
    if distro.is_arch:
        return 'checkupdates; rc=$?; [ "$rc" -eq 0 ] || [ "$rc" -eq 2 ]'
    if distro.is_debian:
        return "apt list --upgradable 2>/dev/null | sed '1d'"
    raise UnsupportedDistroError(f"familia de distro nao suportada: {distro.family}")


def system_query_command(*packages: str) -> list[str]:
    distro = current_distro()
    if distro.is_arch:
        return ["pacman", "-Q", *packages]
    if distro.is_debian:
        return ["dpkg-query", "-W", *packages]
    raise UnsupportedDistroError(f"familia de distro nao suportada: {distro.family}")
