from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


BOOTSTRAP_VERSION = 1


class BootstrapError(RuntimeError):
    pass


@dataclass(frozen=True)
class BootstrapRequirement:
    module_name: str
    system_package: str | None
    aur_package: str | None = None


REQUIREMENTS = (
    BootstrapRequirement("InquirerPy", None, "python-inquirerpy"),
    BootstrapRequirement("pytest", "python-pytest"),
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
        _install_with_pacman(requirements, project_root)
        _install_with_paru(requirements, project_root)
    except (FileNotFoundError, subprocess.CalledProcessError, BootstrapError) as exc:
        missing_names = ", ".join(requirement.module_name for requirement in requirements)
        raise BootstrapError(
            f"falha ao instalar dependencias internas ({missing_names}). "
            "Este ambiente segue PEP 668; use pacman/paru para essas dependencias. "
            "Corrija o problema e execute novamente."
        ) from exc


def _install_with_pacman(requirements: list[BootstrapRequirement], project_root: Path) -> None:
    packages = sorted({requirement.system_package for requirement in requirements if requirement.system_package})
    if not packages:
        return
    if shutil.which("pacman") is None:
        raise BootstrapError("pacman nao esta disponivel para instalar dependencias do sistema.")
    cmd = ["sudo", "pacman", "-S", "--needed", *packages]
    print("$ " + " ".join(cmd))
    subprocess.run(cmd, cwd=str(project_root), check=True)


def _install_with_paru(requirements: list[BootstrapRequirement], project_root: Path) -> None:
    missing_after_pacman = [requirement for requirement in requirements if importlib.util.find_spec(requirement.module_name) is None]
    aur_packages = sorted({requirement.aur_package for requirement in missing_after_pacman if requirement.aur_package})
    if not aur_packages:
        return
    if shutil.which("paru") is None:
        missing_names = ", ".join(requirement.module_name for requirement in missing_after_pacman)
        raise BootstrapError(
            f"faltam dependencias internas ({missing_names}) e elas precisam ser instaladas via AUR/paru neste sistema."
        )
    cmd = ["paru", "-S", "--needed", "--noconfirm", *aur_packages]
    print("$ " + " ".join(cmd))
    subprocess.run(cmd, cwd=str(project_root), check=True)
