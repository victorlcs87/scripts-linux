"""Resolucao de icones para os cards estilo Flathub.

Ordem de resolucao (offline-first, nunca bloqueia a UI):
  1. asset local (ex.: assets/hydra.png) quando `task.icon` e um caminho;
  2. icone do tema do sistema (QIcon.fromTheme) por id Flathub / nome;
  3. avatar tipografico (inicial + cor da categoria) — sempre funciona offline.

O icone do Flathub e baixado em segundo plano por `FlathubIconWorker` e trocado
no card quando chega; se a rede falhar, o avatar continua valendo.
"""

from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QThread, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPixmap

from ..cli import ROOT
from . import theme

_CACHE_DIR = Path.home() / ".cache/reforja/icons"
# Padrao publico de icones do Flathub (best-effort; falha -> mantem avatar).
_FLATHUB_ICON_URL = "https://dl.flathub.org/repo/appstream/x86_64/icons/128x128/{app_id}.png"


def _looks_like_path(value: str) -> bool:
    return value.endswith((".png", ".svg", ".jpg", ".jpeg", ".ico")) or "/" in value


def _looks_like_app_id(value: str) -> bool:
    # Id reverse-DNS do Flatpak: com.exemplo.App (ao menos dois pontos).
    return value.count(".") >= 2 and " " not in value


def _local_pixmap(icon: str, size: int) -> QPixmap | None:
    candidate = Path(icon)
    if not candidate.is_absolute():
        candidate = ROOT / icon
    if candidate.exists():
        pix = QPixmap(str(candidate))
        if not pix.isNull():
            return pix.scaled(
                size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
    return None


def _theme_pixmap(names: list[str], size: int) -> QPixmap | None:
    for name in names:
        if not name:
            continue
        icon = QIcon.fromTheme(name)
        if not icon.isNull():
            pix = icon.pixmap(size, size)
            if not pix.isNull():
                return pix
    return None


def initial_avatar(label: str, category: str, size: int = 48) -> QPixmap:
    """Avatar quadrado arredondado: inicial em branco sobre a cor da categoria."""
    color = QColor(theme.CATEGORY_COLORS.get(category, theme.CATEGORY_COLORS["_default"]))
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QBrush(color))
    painter.setPen(Qt.PenStyle.NoPen)
    radius = size * 0.24
    painter.drawRoundedRect(QRectF(0, 0, size, size), radius, radius)
    letra = (label.strip()[:1] or "?").upper()
    font = QFont()
    font.setPointSizeF(size * 0.42)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor("#ffffff"))
    painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, letra)
    painter.end()
    return pix


def resolve_icon(label: str, icon: str, category: str, size: int = 48) -> QPixmap:
    """Melhor icone disponivel SEM rede: asset local -> cache Flathub -> tema -> avatar.

    `icon` pode ser um caminho de asset local OU um id Flathub (usado para nome de
    tema e para o icone ja cacheado de execucoes anteriores). O download em si e
    feito por FlathubIconWorker, em background; aqui so aproveitamos o cache.
    """
    if icon and _looks_like_path(icon):
        local = _local_pixmap(icon, size)
        if local is not None:
            return local
    if icon and _looks_like_app_id(icon):
        cached = _cache_path(icon)
        if cached.exists():
            local = _local_pixmap(str(cached), size)
            if local is not None:
                return local
    slug = label.lower().replace(" ", "-")
    theme_names = [icon, slug] if icon else [slug]
    from_theme = _theme_pixmap(theme_names, size)
    if from_theme is not None:
        return from_theme
    return initial_avatar(label, category, size)


def _cache_path(app_id: str) -> Path:
    digest = hashlib.sha256(app_id.encode("utf-8")).hexdigest()[:16]
    return _CACHE_DIR / f"{app_id}-{digest}.png"


def flathub_icon_targets(tasks) -> list[tuple[str, str]]:
    """(chave_da_tarefa, app_id) para tarefas cujo `icon` e um id Flathub."""
    targets: list[tuple[str, str]] = []
    for task in tasks:
        icon = getattr(task, "icon", "") or ""
        if icon and not _looks_like_path(icon) and _looks_like_app_id(icon):
            targets.append((task.key, icon))
    return targets


class FlathubIconWorker(QThread):
    """Baixa icones do Flathub em segundo plano e emite (chave, caminho) por acerto."""

    iconReady = Signal(str, str)

    def __init__(self, targets: list[tuple[str, str]]) -> None:
        super().__init__()
        self._targets = targets

    def run(self) -> None:  # noqa: D401 (override QThread.run)
        for key, app_id in self._targets:
            path = _cache_path(app_id)
            if not path.exists():
                if not self._download(app_id, path):
                    continue
            self.iconReady.emit(key, str(path))

    @staticmethod
    def _download(app_id: str, path: Path) -> bool:
        url = _FLATHUB_ICON_URL.format(app_id=app_id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(url, timeout=6) as resp:  # noqa: S310 (host fixo do Flathub)
                if resp.status != 200:
                    return False
                data = resp.read()
            if not data:
                return False
            path.write_bytes(data)
            return True
        except Exception:  # rede/HTTP falhou -> mantem o avatar, sem derrubar a UI
            return False
