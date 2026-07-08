from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .platform import UnsupportedDistroError, detect_distro


class BootstrapError(RuntimeError):
    pass


@dataclass(frozen=True)
class BootstrapRequirement:
    module_name: str
    arch_package: str | None
    debian_package: str | None
    aur_package: str | None = None
    pip_package: str | None = None
    fedora_package: str | None = None

    def system_package_for(self, family: str) -> str | None:
        if family == "arch":
            return self.arch_package
        if family == "debian":
            return self.debian_package
        if family == "fedora":
            return self.fedora_package
        return None

    @property
    def pip_name(self) -> str:
        return self.pip_package or self.module_name


# Somente dependencias de RUNTIME do CLI. Dependencias de desenvolvimento
# (pytest, ruff) vem de `pip install -e .[dev]` e nao sao impostas ao usuario.
REQUIREMENTS = (BootstrapRequirement("InquirerPy", None, None, "python-inquirerpy", "InquirerPy"),)

# Dependencia adicional, instalada sob demanda apenas quando a GUI e solicitada
# (PySide6 e pesado para impor a todos os usuarios do CLI).
GUI_REQUIREMENT = BootstrapRequirement(
    "PySide6", "pyside6", "python3-pyside6", "pyside6", "PySide6", fedora_package="python3-pyside6"
)


def ensure_gui_bootstrap(project_root: Path) -> None:
    """Garante PySide6 disponivel antes de abrir a GUI."""
    if importlib.util.find_spec("PySide6") is not None:
        return
    install_missing_requirements([GUI_REQUIREMENT], project_root)


def ensure_bootstrap(project_root: Path) -> None:
    missing = missing_requirements()
    if missing:
        install_missing_requirements(missing, project_root)


def missing_requirements() -> list[BootstrapRequirement]:
    return [requirement for requirement in REQUIREMENTS if importlib.util.find_spec(requirement.module_name) is None]


def install_missing_requirements(requirements: list[BootstrapRequirement], project_root: Path) -> None:
    if not requirements:
        return
    print("Bootstrap inicial do projeto: instalando dependencias internas...")
    try:
        _install_with_system_package(requirements, project_root)
        _install_with_aur(requirements, project_root)
        _install_with_pip(requirements, project_root)
    except (FileNotFoundError, subprocess.CalledProcessError, BootstrapError, UnsupportedDistroError) as exc:
        missing_names = ", ".join(requirement.module_name for requirement in requirements)
        raise BootstrapError(
            f"falha ao instalar dependencias internas ({missing_names}). Corrija o problema e execute novamente."
        ) from exc


def _install_with_system_package(requirements: list[BootstrapRequirement], project_root: Path) -> None:
    distro = detect_distro()
    if distro.immutable:
        # Sistemas imutaveis (Bazzite/SteamOS): pulamos o gerenciador nativo e
        # deixamos o fallback pip --user resolver as dependencias.
        return
    packages = sorted(
        {
            requirement.system_package_for(distro.family)
            for requirement in requirements
            if requirement.system_package_for(distro.family)
        }
    )
    if not packages:
        return
    if distro.is_arch:
        if shutil.which("pacman") is None:
            raise BootstrapError("pacman nao esta disponivel para instalar dependencias do sistema.")
        cmd = ["sudo", "pacman", "-S", "--needed", *packages]
    elif distro.is_fedora:
        if shutil.which("dnf") is None:
            raise BootstrapError("dnf nao esta disponivel para instalar dependencias do sistema.")
        cmd = ["sudo", "dnf", "install", "-y", *packages]
    else:
        if shutil.which("apt-get") is None:
            raise BootstrapError("apt-get nao esta disponivel para instalar dependencias do sistema.")
        update_cmd = ["sudo", "apt-get", "update"]
        print("$ " + " ".join(update_cmd))
        subprocess.run(update_cmd, cwd=str(project_root), check=True)
        cmd = ["sudo", "apt-get", "install", "-y", *packages]
    print("$ " + " ".join(cmd))
    subprocess.run(cmd, cwd=str(project_root), check=True)


def _install_with_aur(requirements: list[BootstrapRequirement], project_root: Path) -> None:
    distro = detect_distro()
    if not distro.is_arch:
        return
    missing_after_system = [
        requirement for requirement in requirements if importlib.util.find_spec(requirement.module_name) is None
    ]
    aur_packages = sorted({requirement.aur_package for requirement in missing_after_system if requirement.aur_package})
    if not aur_packages:
        return
    helper = shutil.which("paru") or shutil.which("yay")
    if helper is None:
        return
    cmd = [Path(helper).name, "-S", "--needed", "--noconfirm", *aur_packages]
    print("$ " + " ".join(cmd))
    subprocess.run(cmd, cwd=str(project_root), check=True)


def _install_with_pip(requirements: list[BootstrapRequirement], project_root: Path) -> None:
    missing_after_system = [
        requirement for requirement in requirements if importlib.util.find_spec(requirement.module_name) is None
    ]
    if not missing_after_system:
        return
    _ensure_pip_available(project_root)
    packages = [requirement.pip_name for requirement in missing_after_system]
    cmd = [sys.executable, "-m", "pip", "install", "--user", *packages]
    print("$ " + " ".join(cmd))
    subprocess.run(cmd, cwd=str(project_root), check=True)


def _ensure_pip_available(project_root: Path) -> None:
    if importlib.util.find_spec("pip") is not None:
        return
    cmd = [sys.executable, "-m", "ensurepip", "--user"]
    print("$ " + " ".join(cmd))
    subprocess.run(cmd, cwd=str(project_root), check=True)
