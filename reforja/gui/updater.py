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

import hashlib
import json
import os
import ssl
import stat
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ._version import __version__

_REPO = "victorlcs87/scripts-linux"
_API_LATEST = f"https://api.github.com/repos/{_REPO}/releases/latest"
_RELEASES_PAGE = f"https://github.com/{_REPO}/releases/latest"
_TIMEOUT = 8
UPDATED_ENV = "REFORJA_UPDATED_TO"

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


def find_sha256_url(data: dict) -> str:
    """URL do asset SHA256SUMS do release (ou "" quando ausente)."""
    for asset in data.get("assets", []):
        if str(asset.get("name", "")) == "SHA256SUMS":
            return str(asset.get("browser_download_url", ""))
    return ""


def expected_sha256(sums_text: str, filename: str) -> str:
    """Extrai o hash esperado de um arquivo no formato do sha256sum ("hash  nome")."""
    for line in sums_text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == filename:
            return parts[0].lower()
    return ""


def _write_version_file(target: Path, tag: str) -> None:
    """Registra a tag instalada no arquivo .version irmao do AppImage.

    E o mesmo registro que a etapa 15 (Atualizar AppImages) le para decidir se
    precisa baixar. Sem isso, uma atualizacao feita por aqui deixa o .version
    desatualizado e a etapa 15 rebaixa o AppImage inteiro sem necessidade.
    """
    if not tag:
        return
    normalized = tag if tag.startswith("v") else f"v{tag}"
    try:
        target.with_suffix(".version").write_text(normalized + "\n", encoding="utf-8")
    except OSError:
        pass  # registro e best-effort: o download ja foi concluido com sucesso


def running_appimage() -> Path | None:
    """Caminho do AppImage em execucao (variavel APPIMAGE), ou None fora de AppImage."""
    value = os.environ.get("APPIMAGE")
    return Path(value) if value else None


def relaunch_appimage(updated_tag: str = "") -> bool:
    """Lanca o AppImage atualizado num processo novo e desacoplado.

    Nao usamos os.execv: o processo atual ainda segura o mount FUSE da versao
    antiga, entao subimos um processo independente (start_new_session) e deixamos
    o chamador encerrar este. Retorna False se nao estivermos num AppImage ou se
    o lancamento falhar.

    `updated_tag` viaja por REFORJA_UPDATED_TO para a nova instancia avisar que a
    atualizacao concluiu.
    """
    target = running_appimage()
    if target is None or not target.exists():
        return False
    env = dict(os.environ)
    # Variaveis injetadas pelo runtime do AppImage antigo apontam para o mount em
    # uso; limpamos para o novo runtime montar a si mesmo.
    for key in ("APPDIR", "APPIMAGE", "ARGV0", "OWD"):
        env.pop(key, None)
    if updated_tag:
        env[UPDATED_ENV] = updated_tag
    try:
        subprocess.Popen(  # noqa: S603 (caminho vem do proprio runtime do AppImage)
            [str(target)],
            cwd=str(Path.home()),
            env=env,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:  # noqa: BLE001 - sem relançar: o app so avisa e segue aberto
        return False
    return True


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Impede o urllib de seguir o 302: queremos ler o header Location, nao a pagina."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


def _resolve_latest_tag() -> str:
    """Descobre a tag do ultimo release sem a API: releases/latest redireciona
    para releases/tag/vX.Y.Z, e o host github.com nao tem o rate limit de 60/h da
    api.github.com. Retorna "" quando nao ha release."""
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=_ssl_context()))
    req = urllib.request.Request(_RELEASES_PAGE, method="HEAD")
    try:
        with opener.open(req, timeout=_TIMEOUT) as resp:  # noqa: S310 (URL fixa, https)
            resp.read()
        return ""  # sem redirect => sem release publicado
    except urllib.error.HTTPError as exc:
        if exc.code in (301, 302, 303, 307, 308):
            location = exc.headers.get("Location", "")
            return location.rstrip("/").rsplit("/", 1)[-1]
        raise


def _fetch_via_redirect() -> dict:
    """Monta um dict no formato da API a partir apenas da tag: os nomes dos assets
    do release sao deterministicos (ver .github/workflows/ci.yml)."""
    tag = _resolve_latest_tag()
    if not tag:
        raise urllib.error.URLError("nenhum release encontrado")
    version = _norm(tag)
    base = f"https://github.com/{_REPO}/releases/download/{tag}"
    appimage = f"Reforja-{version}-x86_64.AppImage"
    return {
        "tag_name": tag,
        "assets": [
            {"name": appimage, "browser_download_url": f"{base}/{appimage}"},
            {"name": "SHA256SUMS", "browser_download_url": f"{base}/SHA256SUMS"},
        ],
    }


def _fetch_latest() -> dict:
    req = urllib.request.Request(_API_LATEST, headers={"Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT, context=_ssl_context()) as resp:  # noqa: S310 (URL fixa, https)
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Rate limit da api.github.com (60/h por IP sem token): cai para o
        # github.com direto, que nao aplica esse limite.
        if exc.code in (403, 429):
            return _fetch_via_redirect()
        raise


class UpdateChecker(QThread):
    """Checagem automatica no startup. Silenciosa; so emite se houver versao nova."""

    updateAvailable = Signal(str, str, str)  # (tag, url, sha256_url)

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
            self.updateAvailable.emit(tag, url, find_sha256_url(data))


class CheckWorker(QThread):
    """Checagem manual sob demanda. Sempre reporta um resultado."""

    resultReady = Signal(str, str, str, str)  # (status, tag, url, sha256_url) — status: available|current|error

    def __init__(self, current: str = __version__) -> None:
        super().__init__()
        self._current = current

    def run(self) -> None:  # noqa: D401 (override QThread.run)
        try:
            data = _fetch_latest()
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            # Repassa o detalhe real do erro (no campo url) para nao mascarar como "sem rede".
            self.resultReady.emit("error", "", f"{type(exc).__name__}: {exc}", "")
            return
        status, tag, url = parse_release(data, self._current)
        self.resultReady.emit(status, tag, url, find_sha256_url(data))


class DownloadWorker(QThread):
    """Baixa o novo AppImage, valida o SHA256 (quando o release publica o
    SHA256SUMS) e substitui o arquivo alvo de forma atomica."""

    finished = Signal(bool, str)  # (ok, message)
    progress = Signal(int)  # 0-100 (so quando ha Content-Length)

    def __init__(self, url: str, target: Path, sha256_url: str = "", tag: str = "") -> None:
        super().__init__()
        self._url = url
        self._target = target
        self._sha256_url = sha256_url
        self._tag = tag
        self._cancelled = False

    def cancel(self) -> None:
        """Interrompe o download no proximo chunk (usado no fechamento da janela)."""
        self._cancelled = True

    def _expected_hash(self) -> str:
        if not self._sha256_url:
            return ""
        try:
            req = urllib.request.Request(self._sha256_url)
            with urllib.request.urlopen(req, timeout=_TIMEOUT, context=_ssl_context()) as resp:  # noqa: S310
                sums = resp.read().decode("utf-8", errors="ignore")
        except (urllib.error.URLError, TimeoutError, OSError):
            return ""
        filename = self._url.rsplit("/", 1)[-1]
        return expected_sha256(sums, filename)

    def run(self) -> None:  # noqa: D401 (override QThread.run)
        target = self._target
        tmp = target.with_name(target.name + ".new")
        expected = self._expected_hash()
        digest = hashlib.sha256()
        try:
            req = urllib.request.Request(self._url, headers={"Accept": "application/octet-stream"})
            with (
                urllib.request.urlopen(req, timeout=60, context=_ssl_context()) as resp,  # noqa: S310
                open(tmp, "wb") as out,
            ):
                total = int(resp.headers.get("Content-Length") or 0)
                received = 0
                while True:
                    if self._cancelled:
                        raise OSError("download cancelado")
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    out.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    if total:
                        self.progress.emit(int(received / total * 100))
            # Integridade: sem hash conferido, nao substituimos o executavel.
            if expected and digest.hexdigest().lower() != expected:
                raise OSError("SHA256 do download nao confere com o SHA256SUMS do release")
            tmp.chmod(tmp.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            # Rename atomico no mesmo diretorio: a instancia em uso mantem o inode antigo.
            os.replace(tmp, target)
            _write_version_file(target, self._tag)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            self.finished.emit(False, f"Falha ao baixar/instalar a atualizacao: {exc}")
            return
        self.finished.emit(True, str(target))
