"""Atualizacao do app via GitHub Releases.

Duas frentes:
- checagem automatica no startup (UpdateChecker) — apenas avisa;
- checagem/atualizacao manual sob demanda (CheckWorker + DownloadWorker), que
  baixa o novo AppImage e substitui o que esta em execucao (in-place).

Quando o app roda como AppImage, o arquivo em execucao esta em $APPIMAGE; e esse
arquivo que substituimos, de forma atomica (os.replace no mesmo diretorio), para
nao corromper o mount FUSE em uso. A nova versao vale no proximo lancamento.

Tudo falha em silencio/com mensagem (sem rede, sem release, repo privado, etc.).
"""

from __future__ import annotations

import json
import os
import ssl
import stat
import urllib.error
import urllib.request
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ._version import __version__

_API_LATEST = "https://api.github.com/repos/victorlcs87/scripts-linux/releases/latest"
_RELEASES_PAGE = "https://github.com/victorlcs87/scripts-linux/releases/latest"
_TIMEOUT = 8

try:  # certifi garante CAs validas mesmo no AppImage congelado (PyInstaller).
    import certifi

    _CA_FILE: str | None = certifi.where()
except Exception:  # noqa: BLE001 - sem certifi, caimos no contexto padrao do sistema
    _CA_FILE = None


def _ssl_context() -> ssl.SSLContext:
    if _CA_FILE and os.path.exists(_CA_FILE):
        return ssl.create_default_context(cafile=_CA_FILE)
    return ssl.create_default_context()


def _norm(version: str) -> str:
    return version.lstrip("v").strip()


def parse_release(data: dict, current: str) -> tuple[str, str, str]:
    """Interpreta o JSON de releases/latest.

    Retorna (status, tag, appimage_url) com status em {"available", "current"}.
    tag e a versao remota (sem 'v'); url e o asset .AppImage (ou a pagina de
    releases como fallback).
    """
    tag = _norm(str(data.get("tag_name", "")))
    url = _RELEASES_PAGE
    for asset in data.get("assets", []):
        if str(asset.get("name", "")).endswith(".AppImage"):
            url = asset.get("browser_download_url", url)
            break
    if not tag or tag == _norm(current):
        return "current", tag or _norm(current), url
    return "available", tag, url


def running_appimage() -> Path | None:
    """Caminho do AppImage em execucao (variavel APPIMAGE), ou None fora de AppImage."""
    value = os.environ.get("APPIMAGE")
    return Path(value) if value else None


def _fetch_latest() -> dict:
    req = urllib.request.Request(_API_LATEST, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT, context=_ssl_context()) as resp:  # noqa: S310 (URL fixa, https)
        return json.loads(resp.read().decode("utf-8"))


class UpdateChecker(QThread):
    """Checagem automatica no startup. Silenciosa; so emite se houver versao nova."""

    updateAvailable = Signal(str, str)  # (tag, url)

    def __init__(self, current: str = __version__) -> None:
        super().__init__()
        self._current = current

    def run(self) -> None:  # noqa: D401 (override QThread.run)
        if self._current.endswith("-dev"):
            return
        try:
            data = _fetch_latest()
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            return
        status, tag, url = parse_release(data, self._current)
        if status == "available":
            self.updateAvailable.emit(tag, url)


class CheckWorker(QThread):
    """Checagem manual sob demanda. Sempre reporta um resultado."""

    resultReady = Signal(str, str, str)  # (status, tag, url) — status: available|current|error

    def __init__(self, current: str = __version__) -> None:
        super().__init__()
        self._current = current

    def run(self) -> None:  # noqa: D401 (override QThread.run)
        try:
            data = _fetch_latest()
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            # Repassa o detalhe real do erro (no campo url) para nao mascarar como "sem rede".
            self.resultReady.emit("error", "", f"{type(exc).__name__}: {exc}")
            return
        status, tag, url = parse_release(data, self._current)
        self.resultReady.emit(status, tag, url)


class DownloadWorker(QThread):
    """Baixa o novo AppImage e substitui o arquivo alvo de forma atomica."""

    finished = Signal(bool, str)  # (ok, message)

    def __init__(self, url: str, target: Path) -> None:
        super().__init__()
        self._url = url
        self._target = target

    def run(self) -> None:  # noqa: D401 (override QThread.run)
        target = self._target
        tmp = target.with_name(target.name + ".new")
        try:
            req = urllib.request.Request(self._url, headers={"Accept": "application/octet-stream"})
            with (
                urllib.request.urlopen(req, timeout=60, context=_ssl_context()) as resp,  # noqa: S310
                open(tmp, "wb") as out,
            ):
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    out.write(chunk)
            tmp.chmod(tmp.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            # Rename atomico no mesmo diretorio: a instancia em uso mantem o inode antigo.
            os.replace(tmp, target)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            self.finished.emit(False, f"Falha ao baixar/instalar a atualizacao: {exc}")
            return
        self.finished.emit(True, str(target))
