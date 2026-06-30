"""Checagem de atualizacao via GitHub Releases.

Consulta o release mais recente do repositorio e, se a tag for diferente da
versao embutida, sinaliza que ha atualizacao. A checagem roda numa thread e
falha em silencio (sem rede, sem release, etc.).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from PySide6.QtCore import QThread, Signal

from ._version import __version__

_API_LATEST = "https://api.github.com/repos/victorlcs87/scripts-linux/releases/latest"
_RELEASES_PAGE = "https://github.com/victorlcs87/scripts-linux/releases/latest"


class UpdateChecker(QThread):
    updateAvailable = Signal(str, str)  # (tag, url do AppImage ou pagina)

    def __init__(self, current: str = __version__) -> None:
        super().__init__()
        self._current = current

    def run(self) -> None:  # noqa: D401 (override QThread.run)
        if self._current.endswith("-dev"):
            # Build de desenvolvimento: nao incomoda com checagem.
            return
        try:
            req = urllib.request.Request(_API_LATEST, headers={"Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=6) as resp:  # noqa: S310 (URL fixa, https)
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            return
        tag = str(data.get("tag_name", "")).lstrip("v")
        if not tag or tag == self._current.lstrip("v"):
            return
        url = _RELEASES_PAGE
        for asset in data.get("assets", []):
            name = asset.get("name", "")
            if name.endswith(".AppImage"):
                url = asset.get("browser_download_url", url)
                break
        self.updateAvailable.emit(tag, url)
