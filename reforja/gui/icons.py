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
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QThread, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPixmap

from ..cli import ROOT
from . import theme

_CACHE_DIR = Path.home() / ".cache/reforja/icons"
# Padrao publico de icones do Flathub (best-effort; falha -> mantem avatar).
_FLATHUB_ICON_URL = "https://dl.flathub.org/repo/appstream/x86_64/icons/128x128/{app_id}.png"
_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"


def _looks_like_path(value: str) -> bool:
    if value.startswith(("http://", "https://")):
        return False  # e URL remota, resolvida via cache/download
    return value.endswith((".png", ".svg", ".jpg", ".jpeg", ".ico")) or "/" in value


def _looks_like_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


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


# Temas de icones onde procuramos os SVG/PNG diretamente no disco quando o
# QIcon.fromTheme nao resolve (offscreen/sessoes sem plataforma de tema). Ordem
# de preferencia; o primeiro acerto vence.
_ICON_THEME_DIRS = (
    Path.home() / ".local/share/icons",
    Path("/usr/share/icons/breeze"),
    Path("/usr/share/icons/Papirus"),
    Path("/usr/share/icons/Adwaita"),
    Path("/usr/share/icons/hicolor"),
)


@lru_cache(maxsize=256)
def _disk_theme_file(name: str) -> Path | None:
    """Acha o arquivo de icone `name` (.svg/.png) varrendo os temas instalados.

    Fallback robusto para o QIcon.fromTheme, que depende da plataforma de tema
    estar ativa (num app KDE real funciona; offscreen/headless nem sempre).
    O resultado e memoizado (varrer a arvore do tema por card seria caro).
    """
    for root in _ICON_THEME_DIRS:
        if not root.is_dir():
            continue
        for ext in (".svg", ".png"):
            hits = sorted(root.rglob(f"{name}{ext}"))
            if hits:
                return hits[0]
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
    for name in names:
        if not name:
            continue
        found = _disk_theme_file(name)
        if found is not None:
            pix = QPixmap(str(found))
            if not pix.isNull():
                return pix.scaled(
                    size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                )
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
    if icon and (_looks_like_app_id(icon) or _looks_like_url(icon)):
        cached = _cache_path(icon)
        if cached.exists():
            local = _local_pixmap(str(cached), size)
            if local is not None:
                return local
    slug = label.lower().replace(" ", "-")
    tema = "" if _looks_like_url(icon) else icon
    theme_names = [tema, slug] if tema else [slug]
    from_theme = _theme_pixmap(theme_names, size)
    if from_theme is not None:
        return from_theme
    return initial_avatar(label, category, size)


# Icone de tema (freedesktop/Breeze) para as tarefas de configuracao que nao tem
# um icone proprio (id Flathub ou asset). Chaveado por (step_id, task_key). Se o
# tema nao tiver o icone, resolve_icon cai no avatar tipografico — nada quebra.
TASK_THEME_ICONS: dict[tuple[str, str], str] = {
    # 00 Sistema
    ("00", "atualizar"): "system-software-update",
    ("00", "flatpak"): "system-software-install",
    ("00", "appimage-fuse"): "application-x-executable",
    ("00", "aur"): "system-software-install",
    # 03 Navegador + WebApps
    ("03", "navegador"): "internet-web-browser",
    ("03", "webapp-chatgpt"): "internet-web-browser",
    ("03", "webapp-gsv-calendar"): "office-calendar",
    # 05 GPU
    ("05", "driver-amd"): "preferences-desktop-display",
    ("05", "limpeza-nvidia"): "edit-clear-all",
    # 06 Git/GitHub
    ("06", "ferramentas"): "applications-development",
    ("06", "conta"): "preferences-system-users",
    ("06", "repos"): "folder-development",
    # 07 rclone
    ("07", "rclone"): "folder-cloud",
    ("07", "remote"): "folder-network",
    ("07", "servico"): "system-run",
    ("07", "linger"): "chronometer",
    # 08 fstab
    ("08", "fstab"): "drive-harddisk",
    # 09 Ajustes KDE
    ("09", "gestos"): "input-touchpad-on",
    ("09", "numlock"): "input-keyboard",
    # 10 Apps (os sem id Flathub)
    ("10", "Codex CLI"): "utilities-terminal",
    ("10", "auto-cpufreq"): "cpu",
    ("10", "Linux Toys"): "applications-utilities",
    # 12 Antigravity
    ("12", "antigravity"): "applications-development",
    # 13 Sunshine
    ("13", "pacote"): "system-software-install",
    ("13", "grupo-input"): "preferences-system-users",
    ("13", "udev"): "drive-removable-media",
    ("13", "autostart"): "system-run",
    ("13", "launcher"): "applications-games",
    ("13", "ufw"): "preferences-security-firewall",
    ("13", "iniciar"): "media-playback-start",
    # 14 Inventario de hardware
    ("14", "ferramentas"): "utilities-system-monitor",
    ("14", "relatorio"): "text-x-generic",
    # 16 Backup
    ("16", "backup"): "backup",
    ("16", "restore"): "view-restore",
    ("16", "upload"): "cloud-upload",
    ("16", "limpar"): "edit-delete",
}


def resolve_task_icon(step_id: str, task, size: int = 48) -> QPixmap:
    """Como resolve_icon, mas injeta um icone de tema padrao para tarefas sem icone
    proprio (config steps), a partir de TASK_THEME_ICONS — evitando o avatar de letra."""
    icon = getattr(task, "icon", "") or TASK_THEME_ICONS.get((step_id, task.key), "")
    return resolve_icon(task.label, icon, task.category, size)


def _cache_path(app_id: str) -> Path:
    digest = hashlib.sha256(app_id.encode("utf-8")).hexdigest()[:16]
    return _CACHE_DIR / f"{app_id}-{digest}.png"


def remote_icon_targets(tasks) -> list[tuple[str, str]]:
    """(chave_da_tarefa, icone) para tarefas com icone remoto.

    Cobre os dois formatos: id Flathub (vira URL do repo do Flathub) e URL http(s)
    direta — usada pelos WebApps, cujo icone de verdade mora no proprio site.
    """
    targets: list[tuple[str, str]] = []
    for task in tasks:
        icon = getattr(task, "icon", "") or ""
        if not icon or _looks_like_path(icon):
            continue
        if _looks_like_url(icon) or _looks_like_app_id(icon):
            targets.append((task.key, icon))
    return targets


class RemoteIconWorker(QThread):
    """Baixa icones (Flathub ou URL do site) em segundo plano.

    Emite (chave, caminho) por acerto; qualquer falha e silenciosa e o card
    continua com o que ja tinha.
    """

    iconReady = Signal(str, str)

    def __init__(self, targets: list[tuple[str, str]]) -> None:
        super().__init__()
        self._targets = targets

    def run(self) -> None:  # noqa: D401 (override QThread.run)
        for key, icon in self._targets:
            path = _cache_path(icon)
            if not path.exists():
                if not self._download(icon, path):
                    continue
            self.iconReady.emit(key, str(path))

    @staticmethod
    def _download(icon: str, path: Path) -> bool:
        url = icon if _looks_like_url(icon) else _FLATHUB_ICON_URL.format(app_id=icon)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Alguns sites (chatgpt.com) devolvem 403 para User-Agent de script.
            req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
            with urllib.request.urlopen(req, timeout=6) as resp:  # noqa: S310 (http(s) declarado na tarefa)
                if resp.status != 200:
                    return False
                data = resp.read()
            if not data:
                return False
            path.write_bytes(data)
            return True
        except Exception:  # rede/HTTP falhou -> mantem o avatar, sem derrubar a UI
            return False


# Compat: nomes antigos (uma unica fonte era o Flathub).
flathub_icon_targets = remote_icon_targets
FlathubIconWorker = RemoteIconWorker
