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
    system_package: str


REQUIREMENTS = (
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
        "requirements": [requirement.system_package for requirement in REQUIREMENTS],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def missing_requirements() -> list[BootstrapRequirement]:
    return [requirement for requirement in REQUIREMENTS if importlib.util.find_spec(requirement.module_name) is None]


def install_missing_requirements(requirements: list[BootstrapRequirement], project_root: Path) -> None:
    packages = sorted({requirement.system_package for requirement in requirements})
    if not packages:
        return
    if shutil.which("pacman") is None:
        missing_names = ", ".join(requirement.module_name for requirement in requirements)
        raise BootstrapError(
            f"faltam dependencias Python internas ({missing_names}), mas o bootstrap automatico exige pacman neste projeto."
        )
    cmd = ["sudo", "pacman", "-S", "--needed", *packages]
    print("Bootstrap inicial do projeto: instalando dependencias internas...")
    print("$ " + " ".join(cmd))
    try:
        subprocess.run(cmd, cwd=str(project_root), check=True)
    except FileNotFoundError as exc:
        raise BootstrapError("nao consegui iniciar o bootstrap automatico: sudo ou pacman ausente.") from exc
    except subprocess.CalledProcessError as exc:
        packages_text = ", ".join(packages)
        raise BootstrapError(
            f"falha ao instalar dependencias internas via pacman ({packages_text}). "
            "Corrija o problema e execute novamente."
        ) from exc
