"""Instaladores por MECANISMO (Flatpak, npm, assets, JSON remoto).

Instalacao por gerenciador de pacotes da distro (pacman/apt/dnf/AUR) vive em
`platform.py` — importe de la diretamente. Este modulo nao re-exporta platform.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .core import Color, Runner, announce, badge, capture, command_exists
from .platform import _quiet, install_system_or_aur, install_system_package

# Memo por processo: no "Aplicar tudo" varios steps instalam Flatpaks em
# sequencia; garantir o flathub uma unica vez evita reexecutar o remote-add.
_flathub_ready = False


def _reset_ecosystem_cache() -> None:
    """Zera o memo (para testes)."""
    global _flathub_ready
    _flathub_ready = False


def ensure_flatpak(runner: Runner) -> None:
    global _flathub_ready
    if _flathub_ready and not runner.dry_run:
        return
    if not command_exists("flatpak"):
        install_system_package("flatpak", runner)
    runner.run(
        ["flatpak", "remote-add", "--if-not-exists", "flathub", "https://flathub.org/repo/flathub.flatpakrepo"],
        check=False,
        action="Garantindo remote Flathub",
        show_progress=False,
        quiet_success=True,
    )
    if not runner.dry_run:
        _flathub_ready = True


def install_flatpak(app_id: str, runner: Runner) -> None:
    ensure_flatpak(runner)
    if flatpak_installed(app_id):
        announce(runner.logger, "skipped", f"{app_id} ja instalado via Flatpak")
        return
    runner.run(["flatpak", "install", "-y", "flathub", app_id], action=f"Instalando Flatpak {app_id}")


def remove_flatpak(app_id: str, runner: Runner) -> None:
    runner.run(["flatpak", "uninstall", "-y", app_id], check=False, action=f"Removendo Flatpak {app_id}")


def install_system_or_flatpak(system_pkg: str, aur_pkg: str | None, flatpak_id: str, runner: Runner) -> None:
    """Padrao nativo → AUR → Flatpak (qualquer familia, incluindo imutaveis)."""
    if install_system_or_aur(system_pkg, aur_pkg, runner):
        return
    install_flatpak(flatpak_id, runner)


def copy_asset(source: Path, target: Path, runner: Runner) -> None:
    if target.exists() and source.exists() and target.read_bytes() == source.read_bytes():
        announce(runner.logger, "skipped", f"{target} ja esta atualizado")
        return
    if runner.dry_run:
        runner.logger.write(f"{badge('dry-run', Color.DRY_RUN)} cp {source} {target}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def fetch_json(url: str, *, timeout: int = 30) -> dict | list | None:
    """Baixa e parseia um JSON via curl (leitura pura; roda mesmo em dry-run).

    Qualquer falha (rede, timeout, JSON invalido) devolve None para o chamador
    cair no fallback.
    """
    proc = capture(["curl", "-fsSL", url], timeout=timeout)
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def flatpak_installed(app_id: str) -> bool:
    return shutil.which("flatpak") is not None and _quiet(["flatpak", "info", app_id])


def npm_global_installed(pkg: str) -> bool:
    if shutil.which("npm") is None:
        return False
    return _quiet(["npm", "list", "-g", pkg, "--depth=0"])


__all__ = [
    "copy_asset",
    "ensure_flatpak",
    "fetch_json",
    "flatpak_installed",
    "install_flatpak",
    "install_system_or_flatpak",
    "npm_global_installed",
    "remove_flatpak",
]
