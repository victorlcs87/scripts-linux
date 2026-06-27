from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .platform import UnsupportedDistroError, detect_distro


BOOTSTRAP_VERSION = 1


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


REQUIREMENTS = (
    BootstrapRequirement("InquirerPy", None, None, "python-inquirerpy", "InquirerPy"),
    BootstrapRequirement("pytest", "python-pytest", "python3-pytest", None, "pytest", fedora_package="python3-pytest"),
)


def ensure_bootstrap(project_root: Path) -> None:
    state_path = bootstrap_state_path()
    missing = missing_requirements()
    state = load_bootstrap_state(state_path)
    if not missing:
        if state.get("version") != BOOTSTRAP_VERSION:
            write_bootstrap_state(state_path)
        return
    install_missing_requirements(missing, project_root)
    write_bootstrap_state(state_path)


def bootstrap_state_path() -> Path:
    cache_home = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return cache_home / "scripts-linux-postformat" / "bootstrap-state.json"


def load_bootstrap_state(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_bootstrap_state(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": BOOTSTRAP_VERSION,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "requirements": [requirement.module_name for requirement in REQUIREMENTS],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


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
            f"falha ao instalar dependencias internas ({missing_names}). "
            "Corrija o problema e execute novamente."
        ) from exc


def _install_with_system_package(requirements: list[BootstrapRequirement], project_root: Path) -> None:
    distro = detect_distro()
    if distro.immutable:
        # Sistemas imutaveis (Bazzite/SteamOS): pulamos o gerenciador nativo e
        # deixamos o fallback pip --user resolver as dependencias.
        return
    packages = sorted({requirement.system_package_for(distro.family) for requirement in requirements if requirement.system_package_for(distro.family)})
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
    missing_after_system = [requirement for requirement in requirements if importlib.util.find_spec(requirement.module_name) is None]
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
    missing_after_system = [requirement for requirement in requirements if importlib.util.find_spec(requirement.module_name) is None]
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
