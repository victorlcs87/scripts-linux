from __future__ import annotations

import json
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..core import (
    Color,
    announce,
    badge,
    capture,
    confirm_phrase,
    paint,
    prompt_user,
    write_text,
)
from ..installers import flatpak_installed
from ..platform import system_installed
from ..steps_base import Step, StepTask
from ._common import header

# Nome do manifesto gravado dentro do tarball (e ao lado dele, como "ultimo").
MANIFEST_NAME = "reforja-backup.json"
ARCHIVE_PREFIX = "reforja-configs-"
# Cache/dados regeneraveis que nunca entram no backup, para qualquer app Flatpak.
GLOBAL_FLATPAK_EXCLUDES = (
    "*/cache",
    "*/Cache",
    "*/CacheStorage",
    "*/Code Cache",
    "*/GPUCache",
    "*/CachedData",
    "*/Service Worker/CacheStorage",
)


@dataclass(frozen=True)
class BackupEntry:
    """Um conjunto de caminhos de configuracao (relativos a ~) de um app.

    `paths` e o que entra no backup; `excludes` sao globs (relativos a ~) de
    subpastas grandes/regeneraveis a deixar de fora (caches, bibliotecas, perfis).
    `flatpak_id`/`system_pkg`, quando definidos, restringem a captura a maquinas
    onde o app esta de fato instalado.
    """

    app: str
    paths: tuple[str, ...]
    excludes: tuple[str, ...] = ()
    flatpak_id: str | None = None
    system_pkg: str | None = None


def _flatpak(app: str, flatpak_id: str, *, keep_data: bool = True) -> BackupEntry:
    """Entrada para um app Flatpak: config (+ data) sem os caches globais."""
    base = f".var/app/{flatpak_id}"
    paths = (f"{base}/config",) + ((f"{base}/data",) if keep_data else ())
    excludes = tuple(f"{base}/{glob.split('/', 1)[1]}" for glob in GLOBAL_FLATPAK_EXCLUDES)
    return BackupEntry(app=app, paths=paths, excludes=excludes, flatpak_id=flatpak_id)


# Manifesto: so as CONFIGURACOES dos apps que o Reforja instala (nao os apps).
MANIFEST: tuple[BackupEntry, ...] = (
    # --- Flatpaks (config + data, sem cache) ---
    _flatpak("Discord", "com.discordapp.Discord"),
    _flatpak("TeamSpeak", "com.teamspeak.TeamSpeak"),
    _flatpak("ZapZap", "com.rtosta.zapzap"),
    _flatpak("ONLYOFFICE", "org.onlyoffice.desktopeditors"),
    _flatpak("Google Chrome", "com.google.Chrome"),
    _flatpak("Bitwarden", "com.bitwarden.desktop"),
    _flatpak("Flatseal", "com.github.tchx84.Flatseal"),
    _flatpak("Solaar", "io.github.pwr_solaar.solaar"),
    _flatpak("LocalSend", "org.localsend.localsend_app"),
    _flatpak("Heroic", "com.heroicgameslauncher.hgl"),
    # Data gigante/regeneravel: guarda so a config.
    _flatpak("Minecraft Bedrock", "io.mrarm.mcpelauncher", keep_data=False),
    _flatpak("Steam", "com.valvesoftware.Steam", keep_data=False),
    # --- Apps/config nativos ---
    BackupEntry("Git", (".gitconfig",), system_pkg="git"),
    BackupEntry("GitHub CLI", (".config/gh",), system_pkg="github-cli"),
    BackupEntry("rclone", (".config/rclone",), system_pkg="rclone"),
    BackupEntry("Sunshine", (".config/sunshine",), system_pkg="sunshine"),
    BackupEntry(
        "Heroic (nativo)",
        (".config/heroic",),
        excludes=(
            ".config/heroic/Cache",
            ".config/heroic/Partitions",
            ".config/heroic/images-cache",
            ".config/heroic/tools",
            ".config/heroic/store_cache",
            ".config/heroic/GPUCache",
        ),
    ),
    BackupEntry(
        "Discord (nativo)",
        (".config/discord",),
        excludes=(
            ".config/discord/Cache",
            ".config/discord/Code Cache",
            ".config/discord/GPUCache",
            ".config/discord/app-*",
        ),
    ),
    BackupEntry("KDE / gestos", (".config/libinput-gestures.conf", ".config/kcminputrc")),
    # So a definicao dos WebApps do FirefoxPWA; os perfis (GBs) ficam de fora.
    BackupEntry("FirefoxPWA (WebApps)", (".local/share/firefoxpwa/config.json",)),
)


@dataclass
class _Resolved:
    archive: Path
    relpaths: list[str] = field(default_factory=list)
    excludes: list[str] = field(default_factory=list)


class BackupStep(Step):
    id = "16"
    title = "Backup e restore de configuracoes"
    description = (
        "Faz backup so das CONFIGURACOES (nao dos apps) dos programas que o Reforja instala e "
        "restaura de um backup anterior. O restore pergunta antes de sobrescrever e onde esta o "
        "arquivo (util quando o rclone/Google Drive ainda nao esta configurado)."
    )
    # A etapa e acao (backup/restore); nao tem estado de conformidade proprio.
    compliance_from_plan = False

    def tasks(self) -> list[StepTask]:
        return [
            StepTask(
                key="backup",
                label="Fazer backup das configuracoes agora",
                description=(
                    "Empacota as configuracoes dos apps instalados num .tar.gz em "
                    f"{self._backup_dir()} (se o Google Drive estiver montado, cai direto nele). "
                    "Nao inclui os apps, caches, saves de jogos nem perfis pesados."
                ),
                detect=self._last_backup_detail,
                run=self._do_backup,
            ),
            StepTask(
                key="restore",
                label="Restaurar configuracoes de um backup anterior",
                description=(
                    "Pergunta o caminho do backup (.tar.gz ou pasta), mostra o que contem e, apos "
                    "confirmacao, restaura por cima das configuracoes atuais (com copia de seguranca antes)."
                ),
                run=self._do_restore,
                # Acao sob demanda e destrutiva: nunca vem pre-marcada.
                stateless=True,
                destructive=True,
            ),
        ]

    def apply(self) -> None:
        header(self, self.title, "Backup e restore das configuracoes dos apps do Reforja")
        super().apply()

    def status(self) -> None:
        header(self, self.title, "Ultimo backup e itens que entrariam no proximo")
        super().status()
        present = self._present_entries()
        detail = self._last_backup_detail()
        if detail:
            self.ctx.logger.write(f"{badge('backup', Color.SUCCESS)} {detail}")
        else:
            self.ctx.logger.write(f"{badge('backup', Color.WARNING)} nenhum backup encontrado ainda")
        self.ctx.logger.write(paint(f"{len(present)} app(s) com config detectada para o proximo backup:", Color.MUTED))
        for entry, _paths in present:
            self.ctx.logger.write(paint(f"  - {entry.app}", Color.MUTED))
        if present:
            self.mark_applied(f"{len(present)} app(s) com config para backup; {detail or 'sem backup ainda'}.")
        else:
            self.mark_pending("Nenhuma config de app do Reforja encontrada para backup.")

    # ------------------------------------------------------------------ locais

    def _backup_dir(self) -> Path:
        drive = self.ctx.user.home / "GoogleDrive"
        base = drive if drive.is_mount() else self.ctx.user.home
        return base / "reforja-backups"

    def _last_backup(self) -> Path | None:
        backup_dir = self._backup_dir()
        if not backup_dir.is_dir():
            return None
        archives = sorted(backup_dir.glob(f"{ARCHIVE_PREFIX}*.tar.gz"))
        return archives[-1] if archives else None

    def _last_backup_detail(self) -> str | bool:
        latest = self._last_backup()
        if latest is None:
            return False
        when = time.strftime("%d/%m/%Y %H:%M", time.localtime(latest.stat().st_mtime))
        return f"ultimo backup: {when} ({latest.name})"

    # ------------------------------------------------------------------ selecao

    def _present_entries(self) -> list[tuple[BackupEntry, list[str]]]:
        """Entradas do manifesto aplicaveis a esta maquina, com os caminhos que existem."""
        home = self.ctx.user.home
        result: list[tuple[BackupEntry, list[str]]] = []
        for entry in MANIFEST:
            if entry.flatpak_id and not flatpak_installed(entry.flatpak_id):
                continue
            if entry.system_pkg and not system_installed(entry.system_pkg):
                # Config pode existir mesmo sem o pacote (ex.: ~/.gitconfig); nao pular
                # se o caminho esta la.
                if not any((home / rel).exists() for rel in entry.paths):
                    continue
            existing = [rel for rel in entry.paths if (home / rel).exists()]
            if existing:
                result.append((entry, existing))
        return result

    def _relpaths_and_excludes(self) -> tuple[list[str], list[str]]:
        relpaths: list[str] = []
        excludes: list[str] = []
        for entry, paths in self._present_entries():
            relpaths.extend(paths)
            excludes.extend(entry.excludes)
        return relpaths, excludes

    # ------------------------------------------------------------------ backup

    def _tar_create_cmd(self, archive: Path, relpaths: list[str], excludes: list[str], manifest_dir: Path) -> list[str]:
        cmd = ["tar", "-czf", str(archive)]
        for glob in excludes:
            cmd.append(f"--exclude={glob}")
        cmd += ["-C", str(self.ctx.user.home), *relpaths]
        # Muda de diretorio para incluir o manifesto no topo do arquivo.
        cmd += ["-C", str(manifest_dir), MANIFEST_NAME]
        return cmd

    def _write_manifest(self, backup_dir: Path, present: list[tuple[BackupEntry, list[str]]]) -> Path:
        manifest = {
            "version": 1,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "host": socket.gethostname(),
            "user": self.ctx.user.name,
            "apps": [{"app": entry.app, "paths": paths} for entry, paths in present],
        }
        path = backup_dir / MANIFEST_NAME
        write_text(path, json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", self.ctx.runner)
        return path

    def _create_archive(self, archive: Path, present: list[tuple[BackupEntry, list[str]]]) -> None:
        backup_dir = archive.parent
        if not self.ctx.runner.dry_run:
            backup_dir.mkdir(parents=True, exist_ok=True)
        self._write_manifest(backup_dir, present)
        relpaths: list[str] = []
        excludes: list[str] = []
        for entry, paths in present:
            relpaths.extend(paths)
            excludes.extend(entry.excludes)
        self.ctx.runner.run(
            self._tar_create_cmd(archive, relpaths, excludes, backup_dir),
            action=f"Compactando configuracoes em {archive.name}",
        )

    def _do_backup(self) -> None:
        present = self._present_entries()
        if not present:
            announce(self.ctx.logger, "skipped", "Nenhuma configuracao de app do Reforja encontrada para backup.")
            self.mark_skipped("Nada para fazer backup nesta maquina.")
            return
        backup_dir = self._backup_dir()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        archive = backup_dir / f"{ARCHIVE_PREFIX}{stamp}.tar.gz"
        self.ctx.logger.write(paint(f"Incluindo config de {len(present)} app(s):", Color.MUTED))
        for entry, _paths in present:
            self.ctx.logger.write(paint(f"  - {entry.app}", Color.MUTED))
        self._create_archive(archive, present)
        announce(self.ctx.logger, "done", f"Backup gravado em {archive}")
        self.add_hint(f"Sincronize {backup_dir} com o Google Drive (etapa 07 monta em ~/GoogleDrive).")
        self.mark_done(f"Backup das configuracoes criado em {archive.name}.")

    # ------------------------------------------------------------------ restore

    def _resolve_archive(self, raw: str) -> Path | None:
        candidate = Path(raw).expanduser()
        if candidate.is_dir():
            archives = sorted(candidate.glob(f"{ARCHIVE_PREFIX}*.tar.gz")) or sorted(candidate.glob("*.tar.gz"))
            if not archives:
                announce(self.ctx.logger, "warning", f"Nenhum .tar.gz encontrado em {candidate}.")
                return None
            return archives[-1]
        if candidate.is_file():
            return candidate
        announce(self.ctx.logger, "warning", f"Caminho nao encontrado: {candidate}.")
        return None

    def _read_backup_manifest(self, archive: Path) -> dict | None:
        proc = capture(["tar", "-xzOf", str(archive), MANIFEST_NAME])
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def _do_restore(self) -> None:
        default_dir = self._backup_dir()
        raw = prompt_user(
            "Caminho do backup para restaurar (.tar.gz ou pasta); vazio para pular",
            self.ctx.logger,
            detail=(
                f"Sugestao: {default_dir}. Se o backup esta no Google Drive e o rclone ainda nao "
                "esta configurado, baixe o arquivo manualmente e informe o caminho aqui."
            ),
            prompt_label="Caminho",
        ).strip()
        if not raw:
            announce(self.ctx.logger, "skipped", "Restore pulado; nenhuma configuracao foi alterada.")
            self.mark_skipped("Restore nao solicitado (caminho vazio).")
            return

        archive = self._resolve_archive(raw)
        if archive is None:
            self.mark_manual("Restore abortado: caminho de backup invalido.")
            return

        manifest = self._read_backup_manifest(archive)
        if manifest is None:
            announce(
                self.ctx.logger,
                "warning",
                f"{archive.name} nao parece um backup do Reforja (sem {MANIFEST_NAME}). Restore abortado.",
            )
            self.mark_manual("Restore abortado: arquivo nao e um backup valido do Reforja.")
            return

        apps = [item.get("app", "?") for item in manifest.get("apps", [])]
        self.ctx.logger.write(
            f"{badge('backup', Color.INFO)} {archive.name} - criado em {manifest.get('created', '?')} "
            f"em {manifest.get('host', '?')}"
        )
        self.ctx.logger.write(paint(f"Contem config de {len(apps)} app(s): {', '.join(apps) or '-'}", Color.MUTED))

        if not confirm_phrase("restaurar", self.ctx.logger):
            announce(self.ctx.logger, "skipped", "Confirmacao nao recebida; restore cancelado.")
            self.mark_skipped("Restore cancelado pelo usuario.")
            return

        self._safety_backup()
        self.ctx.runner.run(
            ["tar", "-xzf", str(archive), f"--exclude={MANIFEST_NAME}", "-C", str(self.ctx.user.home)],
            action=f"Restaurando configuracoes de {archive.name}",
        )
        announce(self.ctx.logger, "done", "Configuracoes restauradas.")
        self.add_hint("Feche e reabra os apps afetados para carregarem as configuracoes restauradas.")
        self.mark_done(f"Configuracoes restauradas a partir de {archive.name}.")

    def _safety_backup(self) -> None:
        """Snapshot das configs atuais antes de sobrescrever, para o restore ser reversivel."""
        present = self._present_entries()
        if not present:
            return
        stamp = time.strftime("%Y%m%d-%H%M%S")
        safety = self._backup_dir() / f"pre-restore-{stamp}.tar.gz"
        self._create_archive(safety, present)
        announce(self.ctx.logger, "done", f"Copia de seguranca das configs atuais em {safety.name}")
