"""Preferencias persistentes da GUI (tema, console) em ~/.config/reforja/gui.json.

Minimo e tolerante a falha: se o arquivo nao existir ou estiver corrompido,
volta aos padroes. Nao depende do Runner nem do motor — e so a camada de UI.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_DEFAULTS: dict[str, Any] = {
    "theme": "light",  # "light" | "dark"
    "console_collapsed": False,
}


def _config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "reforja" / "gui.json"


def load() -> dict[str, Any]:
    """Le as preferencias; qualquer problema devolve os padroes (nunca levanta)."""
    data = dict(_DEFAULTS)
    try:
        raw = json.loads(_config_path().read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            data.update({key: raw[key] for key in _DEFAULTS if key in raw})
    except (OSError, ValueError):
        pass
    return data


def save(values: dict[str, Any]) -> None:
    """Grava as preferencias conhecidas (ignora falha de escrita silenciosamente)."""
    path = _config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        current = load()
        current.update({key: values[key] for key in _DEFAULTS if key in values})
        path.write_text(json.dumps(current, indent=2), encoding="utf-8")
    except OSError:
        pass
