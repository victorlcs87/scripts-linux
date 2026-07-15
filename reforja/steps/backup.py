from __future__ import annotations

import getpass
import json
import os
import socket
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..core import (
    Color,
    announce,
    badge,
    capture,
    command_exists,
    confirm_phrase,
    paint,
    prompt_user,
    select_many,
    write_text,
)
from ..installers import flatpak_installed
from ..platform import install_first_available, system_installed
from ..steps_base import Step, StepTask
from ._common import header

# Nome do manifesto gravado dentro do tarball (e ao lado dele, como "ultimo").
MANIFEST_NAME = "reforja-backup.json"
ARCHIVE_PREFIX = "reforja-configs-"
PLAIN_SUFFIX = ".tar.gz"
ENC_SUFFIX = ".tar.gz.gpg"
# Quantos backups/copias de seguranca manter (os mais antigos sao removidos).
KEEP_BACKUPS = 3
KEEP_SAFETY = 3
# Flatpaks cujo `data` e gigante/regeneravel: guarda so a config.
HEAVY_DATA_FLATPAKS = frozenset({"io.mrarm.mcpelauncher", "com.valvesoftware.Steam"})
# Flatpaks que o Reforja instala mas nao carregam flatpak_id no dicionario da
# etapa 10 (Steam so cai no Flatpak como fallback).
EXTRA_FLATPAKS = {"Steam": "com.valvesoftware.Steam"}
# Apps cuja config guarda segredos (tokens/sessoes): a criptografia vira padrao
# forte quando algum deles entra no backup.
SENSITIVE_APPS = frozenset({"rclone", "Bitwarden", "Google Chrome", "Discord", "Discord (nativo)", "GitHub CLI"})
# Cache/dados regeneraveis que nunca entram no backup, para qualquer app Flatpak.
GLOBAL_FLATPAK_EXCLUDES = (
    "cache",
    "Cache",
    "CacheStorage",
    "Code Cache",
    "GPUCache",
    "CachedData",
    "Service Worker/CacheStorage",
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
    excludes = tuple(f"{base}/*/{glob}" for glob in GLOBAL_FLATPAK_EXCLUDES)
    return BackupEntry(app=app, paths=paths, excludes=excludes, flatpak_id=flatpak_id)


def _derive_flatpak_entries() -> list[BackupEntry]:
    """Deriva os Flatpaks do dicionario de apps da etapa 10 (fonte unica).

    Evita a lista duplicada: um app novo adicionado la ja entra no backup.
    """
    from .gaming import AppsStep  # import tardio para nao criar ciclo

    entries: list[BackupEntry] = []
    seen: set[str] = set()
    for app_name, definition in AppsStep.apps.items():
        flatpak_id = definition.get("flatpak_id")
        if not flatpak_id:
            continue
        entries.append(_flatpak(app_name, flatpak_id, keep_data=flatpak_id not in HEAVY_DATA_FLATPAKS))
        seen.add(flatpak_id)
    for app_name, flatpak_id in EXTRA_FLATPAKS.items():
        if flatpak_id not in seen:
            entries.append(_flatpak(app_name, flatpak_id, keep_data=flatpak_id not in HEAVY_DATA_FLATPAKS))
    return entries


# Configuracoes nativas (fora do modelo Flatpak) dos apps que o Reforja mexe.
NATIVE_ENTRIES: tuple[BackupEntry, ...] = (
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


def _manifest_entries() -> list[BackupEntry]:
    return _derive_flatpak_entries() + list(NATIVE_ENTRIES)


@dataclass
class _Prepared:
    """Resultado de preparar um restore: o tar em claro e o que apagar depois."""

    plain: Path
    cleanup: list[Path] = field(default_factory=list)


class BackupStep(Step):
    id = "16"
    title = "Backup e restore de configuracoes"
    description = (
        "Faz backup so das CONFIGURACOES (nao dos apps) dos programas que o Reforja instala e "
        "restaura de um backup anterior. Pode cifrar o arquivo com senha (recomendado, pois ele "
        "guarda tokens e sessoes). O restore pergunta antes de sobrescrever e onde esta o arquivo "
        "(util quando o rclone/Google Drive ainda nao esta configurado)."
    )
    # A etapa e acao (backup/restore); nao tem estado de conformidade proprio.
    compliance_from_plan = False

    def tasks(self) -> list[StepTask]:
        return [
            StepTask(
                key="backup",
                label="Fazer backup das configuracoes agora",
                description=(
                    "Empacota as configuracoes dos apps instalados num .tar.gz (cifrado com senha, se "
                    f"voce quiser) em {self._backup_dir()} — cai direto no Google Drive quando montado. "
                    "Nao inclui os apps, caches, saves de jogos nem perfis pesados."
                ),
                detect=self._last_backup_detail,
                run=self._do_backup,
                # Backup e acao de "antes de formatar": nao faz sentido rodar sozinho
                # no "Aplicar tudo" numa maquina recem-instalada.
                autoselect=False,
            ),
            StepTask(
                key="restore",
                label="Restaurar configuracoes de um backup anterior",
                description=(
                    "Pergunta o caminho do backup (.tar.gz ou .tar.gz.gpg), deixa escolher quais apps "
                    "restaurar e, apos confirmacao, restaura por cima das configuracoes atuais (com "
                    "copia de seguranca antes). Se o arquivo for cifrado, pede a senha."
                ),
                run=self._do_restore,
                # Acao sob demanda e destrutiva: nunca vem pre-marcada.
                stateless=True,
                destructive=True,
            ),
            StepTask(
                key="upload",
                label="Enviar ultimo backup para o Google Drive (rclone)",
                description=(
                    "Faz 'rclone copy' do backup mais recente para um caminho remoto do Drive. Funciona "
                    "mesmo sem ~/GoogleDrive montado; precisa do rclone configurado (etapa 07)."
                ),
                run=self._do_upload,
                stateless=True,
                autoselect=False,
            ),
            StepTask(
                key="limpar",
                label="Limpar backups antigos",
                description=(
                    f"Mantem apenas os {KEEP_BACKUPS} backups mais recentes em {self._backup_dir()} e "
                    "remove os anteriores para nao acumular espaco (no disco e no Google Drive)."
                ),
                run=self._do_prune,
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

    def _safety_dir(self) -> Path:
        """Copias pre-restore ficam SEMPRE locais (nunca no Drive) e em claro."""
        return self.ctx.user.home / ".local/state/reforja/pre-restore"

    def _tmp_dir(self) -> Path:
        """Area local para tar em claro e arquivos de senha (nunca no Drive)."""
        return self.ctx.user.home / ".cache/reforja/backup-tmp"

    def _list_backups(self) -> list[Path]:
        backup_dir = self._backup_dir()
        if not backup_dir.is_dir():
            return []
        found = list(backup_dir.glob(f"{ARCHIVE_PREFIX}*{PLAIN_SUFFIX}"))
        found += list(backup_dir.glob(f"{ARCHIVE_PREFIX}*{ENC_SUFFIX}"))
        return sorted(found, key=lambda p: p.stat().st_mtime)

    def _last_backup(self) -> Path | None:
        backups = self._list_backups()
        return backups[-1] if backups else None

    def _last_backup_detail(self) -> str | bool:
        latest = self._last_backup()
        if latest is None:
            return False
        when = time.strftime("%d/%m/%Y %H:%M", time.localtime(latest.stat().st_mtime))
        cofre = " [cifrado]" if latest.name.endswith(ENC_SUFFIX) else ""
        return f"ultimo backup: {when} ({latest.name}){cofre}"

    # ------------------------------------------------------------------ selecao

    def _present_entries(self) -> list[tuple[BackupEntry, list[str]]]:
        """Entradas do manifesto aplicaveis a esta maquina, com os caminhos que existem."""
        home = self.ctx.user.home
        result: list[tuple[BackupEntry, list[str]]] = []
        for entry in _manifest_entries():
            if entry.flatpak_id and not flatpak_installed(entry.flatpak_id):
                continue
            if entry.system_pkg and not system_installed(entry.system_pkg):
                # Config pode existir mesmo sem o pacote (ex.: ~/.gitconfig);
                # so pula se nenhum caminho estiver presente.
                if not any((home / rel).exists() for rel in entry.paths):
                    continue
            existing = [rel for rel in entry.paths if (home / rel).exists()]
            if existing:
                result.append((entry, existing))
        return result

    def _choose_present(
        self, present: list[tuple[BackupEntry, list[str]]], *, prompt: str
    ) -> list[tuple[BackupEntry, list[str]]]:
        if len(present) <= 1:
            return present
        indices = select_many(
            prompt,
            [entry.app for entry, _paths in present],
            self.ctx.logger,
            detail="Desmarque o que nao quer incluir.",
            preselected=list(range(len(present))),
        )
        return [present[i] for i in indices]

    @staticmethod
    def _flatten(present: list[tuple[BackupEntry, list[str]]]) -> tuple[list[str], list[str]]:
        relpaths: list[str] = []
        excludes: list[str] = []
        for entry, paths in present:
            relpaths.extend(paths)
            excludes.extend(entry.excludes)
        return relpaths, excludes

    def _relpaths_and_excludes(self) -> tuple[list[str], list[str]]:
        return self._flatten(self._present_entries())

    # ------------------------------------------------------------------ tamanho

    def _estimate_size(self, present: list[tuple[BackupEntry, list[str]]]) -> str | None:
        relpaths, excludes = self._flatten(present)
        if not relpaths:
            return None
        cmd = ["du", "-shc"] + [f"--exclude={glob}" for glob in excludes] + ["--", *relpaths]
        proc = capture(cmd, cwd=self.ctx.user.home)
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        return proc.stdout.strip().splitlines()[-1].split("\t")[0].strip()

    # ------------------------------------------------------------------ cripto

    def _want_encryption(self, sensitive: bool) -> bool:
        chosen = select_many(
            "Cifrar o backup com senha?",
            ["Cifrar com senha (recomendado: o backup guarda tokens e sessoes)"],
            self.ctx.logger,
            detail="Se desmarcar, o arquivo fica em texto puro.",
            preselected=[0],
        )
        encrypt = bool(chosen)
        if not encrypt and sensitive:
            announce(
                self.ctx.logger,
                "warning",
                "Este backup inclui tokens/sessoes (rclone, Bitwarden ou navegador). Gravar SEM cifrar "
                "expoe esses dados no Google Drive.",
            )
            if not confirm_phrase("gravar sem cifrar", self.ctx.logger):
                announce(self.ctx.logger, "done", "Mantendo a criptografia ligada.")
                encrypt = True
        if not encrypt:
            return False
        if not command_exists("gpg"):
            announce(self.ctx.logger, "warning", "gpg nao encontrado; tentando instalar o gnupg.")
            install_first_available(("gnupg", "gnupg2", "gpg"), self.ctx.runner)
        if not command_exists("gpg") and not self.ctx.runner.dry_run:
            announce(self.ctx.logger, "warning", "gpg indisponivel; o backup sera gravado SEM criptografia.")
            return False
        return True

    def _read_passphrase(self, *, confirm: bool) -> str:
        logger = self.ctx.logger
        if logger.interaction is not None:
            ask_secret = getattr(logger.interaction, "ask_secret", None) or logger.interaction.ask_text
            return ask_secret(
                "Senha para o backup cifrado",
                detail="Guarde essa senha: sem ela o backup nao pode ser restaurado.",
                prompt_label="Senha",
            )
        while True:
            first = getpass.getpass("Senha para o backup cifrado: ")
            if not first:
                self.ctx.logger.write("Senha vazia; tente novamente.")
                continue
            if not confirm:
                return first
            if first == getpass.getpass("Repita a senha: "):
                return first
            self.ctx.logger.write("As senhas nao coincidem; tente novamente.")

    def _passphrase_file(self, passphrase: str) -> Path:
        tmp = self._tmp_dir()
        tmp.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=tmp, prefix="pf-")
        os.write(fd, passphrase.encode("utf-8"))
        os.close(fd)
        os.chmod(name, 0o600)
        return Path(name)

    def _gpg_cmd(self, args: list[str], passfile: Path) -> list[str]:
        return ["gpg", "--batch", "--yes", "--pinentry-mode", "loopback", "--passphrase-file", str(passfile), *args]

    # ------------------------------------------------------------------ tar

    def _tar_create_cmd(self, archive: Path, relpaths: list[str], excludes: list[str], manifest_dir: Path) -> list[str]:
        cmd = ["tar", "-czf", str(archive)]
        for glob in excludes:
            cmd.append(f"--exclude={glob}")
        cmd += ["-C", str(self.ctx.user.home), *relpaths]
        # Muda de diretorio para incluir o manifesto no topo do arquivo.
        cmd += ["-C", str(manifest_dir), MANIFEST_NAME]
        return cmd

    def _write_manifest(
        self, manifest_dir: Path, present: list[tuple[BackupEntry, list[str]]], *, encrypted: bool
    ) -> None:
        manifest = {
            "version": 1,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "host": socket.gethostname(),
            "user": self.ctx.user.name,
            "encrypted": encrypted,
            "apps": [{"app": entry.app, "paths": paths} for entry, paths in present],
        }
        write_text(
            manifest_dir / MANIFEST_NAME, json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", self.ctx.runner
        )

    def _create_plain_archive(
        self, archive: Path, present: list[tuple[BackupEntry, list[str]]], *, encrypted: bool
    ) -> None:
        manifest_dir = archive.parent
        if not self.ctx.runner.dry_run:
            manifest_dir.mkdir(parents=True, exist_ok=True)
        self._write_manifest(manifest_dir, present, encrypted=encrypted)
        relpaths, excludes = self._flatten(present)
        self.ctx.runner.run(
            self._tar_create_cmd(archive, relpaths, excludes, manifest_dir),
            action=f"Compactando configuracoes em {archive.name}",
        )

    def _verify_tar(self, plain: Path) -> bool:
        if self.ctx.runner.dry_run:
            return True
        proc = capture(["tar", "-tzf", str(plain)])
        return proc.returncode == 0 and bool(proc.stdout.strip())

    # ------------------------------------------------------------------ backup

    def _do_backup(self) -> None:
        present = self._present_entries()
        if not present:
            announce(self.ctx.logger, "skipped", "Nenhuma configuracao de app do Reforja encontrada para backup.")
            self.mark_skipped("Nada para fazer backup nesta maquina.")
            return
        present = self._choose_present(present, prompt="Quais apps incluir no backup?")
        if not present:
            self.mark_skipped("Nenhum app marcado; backup cancelado.")
            return

        estimate = self._estimate_size(present)
        if estimate:
            self.ctx.logger.write(paint(f"Tamanho estimado (sem caches): ~{estimate}", Color.MUTED))
        self.ctx.logger.write(paint(f"Incluindo config de {len(present)} app(s):", Color.MUTED))
        for entry, _paths in present:
            self.ctx.logger.write(paint(f"  - {entry.app}", Color.MUTED))

        sensitive = any(entry.app in SENSITIVE_APPS for entry, _paths in present)
        encrypt = self._want_encryption(sensitive)
        backup_dir = self._backup_dir()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        if not self.ctx.runner.dry_run:
            backup_dir.mkdir(parents=True, exist_ok=True)

        if not encrypt:
            archive = backup_dir / f"{ARCHIVE_PREFIX}{stamp}{PLAIN_SUFFIX}"
            self._create_plain_archive(archive, present, encrypted=False)
        else:
            archive = self._create_encrypted(backup_dir / f"{ARCHIVE_PREFIX}{stamp}{ENC_SUFFIX}", present, stamp)
            if archive is None:
                self.mark_manual("Backup abortado: falha ao cifrar.")
                return

        announce(self.ctx.logger, "done", f"Backup gravado em {archive}")
        self.add_hint(f"Sincronize {backup_dir} com o Google Drive (etapa 07 monta em ~/GoogleDrive).")
        self._prune_dir(
            backup_dir, (f"{ARCHIVE_PREFIX}*{PLAIN_SUFFIX}", f"{ARCHIVE_PREFIX}*{ENC_SUFFIX}"), KEEP_BACKUPS
        )
        self.mark_done(f"Backup das configuracoes criado em {archive.name}.")

    def _create_encrypted(self, final: Path, present: list[tuple[BackupEntry, list[str]]], stamp: str) -> Path | None:
        """Cria o tar em claro numa area LOCAL, cifra para `final` e apaga o claro."""
        passphrase = self._read_passphrase(confirm=True)
        tmp = self._tmp_dir()
        if not self.ctx.runner.dry_run:
            tmp.mkdir(parents=True, exist_ok=True)
        plain = tmp / f"{ARCHIVE_PREFIX}{stamp}{PLAIN_SUFFIX}"
        passfile = None if self.ctx.runner.dry_run else self._passphrase_file(passphrase)
        try:
            self._create_plain_archive(plain, present, encrypted=True)
            if not self._verify_tar(plain):
                announce(self.ctx.logger, "warning", "O tar gerado nao passou na verificacao; abortando.")
                return None
            self.ctx.runner.run(
                self._gpg_cmd(["--cipher-algo", "AES256", "-o", str(final), "-c", str(plain)], passfile or plain),
                action=f"Cifrando backup em {final.name}",
            )
            if not self.ctx.runner.dry_run and not final.exists():
                announce(self.ctx.logger, "warning", "gpg nao produziu o arquivo cifrado; abortando.")
                return None
            # Confere que o arquivo cifrado realmente abre com a senha, agora e nao
            # so na hora do restore.
            if passfile is not None and not self._verify_encrypted(final, passfile):
                announce(self.ctx.logger, "warning", "O backup cifrado nao passou na verificacao; abortando.")
                self._secure_unlink(final)
                return None
            return final
        finally:
            self._secure_unlink(plain)
            if passfile is not None:
                self._secure_unlink(passfile)

    def _verify_encrypted(self, final: Path, passfile: Path) -> bool:
        """Decifra para um temporario e confere que e um tar valido (round-trip)."""
        if self.ctx.runner.dry_run:
            return True
        check = self._tmp_dir() / f"{final.stem}.verify"
        result = self.ctx.runner.run(
            self._gpg_cmd(["-o", str(check), "-d", str(final)], passfile),
            check=False,
            show_progress=False,
            quiet_success=True,
            action="Conferindo o backup cifrado",
        )
        ok = (result is None or result.returncode == 0) and self._verify_tar(check)
        self._secure_unlink(check)
        return ok

    def _secure_unlink(self, path: Path) -> None:
        if self.ctx.runner.dry_run:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    # ------------------------------------------------------------------ retencao

    def _prune_dir(self, directory: Path, patterns: tuple[str, ...], keep: int) -> int:
        if self.ctx.runner.dry_run or not directory.is_dir():
            return 0
        found: list[Path] = []
        for pattern in patterns:
            found.extend(directory.glob(pattern))
        found.sort(key=lambda p: p.stat().st_mtime)
        removed = 0
        for old in found[:-keep] if keep > 0 else found:
            try:
                old.unlink()
                removed += 1
                self.ctx.logger.write(f"{badge('removido', Color.MUTED)} {old.name}")
            except OSError:
                pass
        return removed

    def _do_prune(self) -> None:
        removed = self._prune_dir(
            self._backup_dir(), (f"{ARCHIVE_PREFIX}*{PLAIN_SUFFIX}", f"{ARCHIVE_PREFIX}*{ENC_SUFFIX}"), KEEP_BACKUPS
        )
        removed += self._prune_dir(self._safety_dir(), ("pre-restore-*",), KEEP_SAFETY)
        if removed:
            self.mark_done(f"{removed} backup(s) antigo(s) removido(s); mantidos os {KEEP_BACKUPS} mais recentes.")
        else:
            self.mark_skipped(f"Nada a limpar; ha no maximo {KEEP_BACKUPS} backups.")

    # ------------------------------------------------------------------ upload

    def _default_remote(self) -> str:
        from .storage import RcloneStep  # import tardio para nao criar ciclo

        return f"{RcloneStep.remote}reforja-backups"

    def _do_upload(self) -> None:
        if not command_exists("rclone") and not self.ctx.runner.dry_run:
            announce(self.ctx.logger, "warning", "rclone nao encontrado. Rode a etapa 07 antes de enviar.")
            self.mark_manual("Upload abortado: rclone nao instalado.")
            return
        latest = self._last_backup()
        if latest is None:
            announce(self.ctx.logger, "skipped", "Nenhum backup local encontrado para enviar.")
            self.mark_skipped("Nada para enviar; faca um backup primeiro.")
            return
        default = self._default_remote()
        dest = (
            prompt_user(
                "Destino no rclone (Enter para o padrao)",
                self.ctx.logger,
                detail=f"Padrao: {default}. Formato: 'Remote:pasta' (o remote vem da etapa 07).",
                prompt_label="Destino",
            ).strip()
            or default
        )
        self.ctx.runner.run(
            ["rclone", "copy", str(latest), dest],
            action=f"Enviando {latest.name} para {dest}",
        )
        announce(self.ctx.logger, "done", f"{latest.name} enviado para {dest}")
        self.mark_done(f"Backup {latest.name} enviado para {dest} via rclone.")

    # ------------------------------------------------------------------ restore

    def _resolve_archive(self, raw: str) -> Path | None:
        candidate = Path(raw).expanduser()
        if candidate.is_dir():
            archives = sorted(
                [*candidate.glob(f"*{ENC_SUFFIX}"), *candidate.glob(f"*{PLAIN_SUFFIX}")],
                key=lambda p: p.stat().st_mtime,
            )
            if not archives:
                announce(self.ctx.logger, "warning", f"Nenhum backup encontrado em {candidate}.")
                return None
            return archives[-1]
        if candidate.is_file():
            return candidate
        announce(self.ctx.logger, "warning", f"Caminho nao encontrado: {candidate}.")
        return None

    def _prepare_plain(self, archive: Path) -> _Prepared | None:
        """Devolve o tar em claro (decifrando se necessario) e o que apagar depois."""
        if not archive.name.endswith(ENC_SUFFIX):
            return _Prepared(plain=archive)
        if not command_exists("gpg"):
            announce(self.ctx.logger, "warning", "Backup cifrado, mas o gpg nao esta instalado. Restore abortado.")
            return None
        passphrase = self._read_passphrase(confirm=False)
        tmp = self._tmp_dir()
        tmp.mkdir(parents=True, exist_ok=True)
        plain = tmp / (archive.name[: -len(ENC_SUFFIX)] + PLAIN_SUFFIX)
        passfile = self._passphrase_file(passphrase)
        result = self.ctx.runner.run(
            self._gpg_cmd(["-o", str(plain), "-d", str(archive)], passfile),
            check=False,
            action=f"Decifrando {archive.name}",
        )
        self._secure_unlink(passfile)
        if result is not None and result.returncode != 0:
            announce(self.ctx.logger, "warning", "Nao consegui decifrar (senha errada?). Restore abortado.")
            self._secure_unlink(plain)
            return None
        return _Prepared(plain=plain, cleanup=[plain])

    def _read_backup_manifest(self, plain: Path) -> dict | None:
        proc = capture(["tar", "-xzOf", str(plain), MANIFEST_NAME])
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
            "Caminho do backup para restaurar (.tar.gz/.tar.gz.gpg ou pasta); vazio para pular",
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

        prepared = self._prepare_plain(archive)
        if prepared is None:
            self.mark_manual("Restore abortado: nao foi possivel abrir o backup.")
            return
        try:
            self._restore_from_plain(archive, prepared.plain)
        finally:
            for path in prepared.cleanup:
                self._secure_unlink(path)

    def _restore_from_plain(self, archive: Path, plain: Path) -> None:
        manifest = self._read_backup_manifest(plain)
        if manifest is None:
            announce(
                self.ctx.logger,
                "warning",
                f"{archive.name} nao parece um backup do Reforja (sem {MANIFEST_NAME}). Restore abortado.",
            )
            self.mark_manual("Restore abortado: arquivo nao e um backup valido do Reforja.")
            return
        if not self._verify_tar(plain):
            announce(self.ctx.logger, "warning", f"{archive.name} esta corrompido (tar ilegivel). Restore abortado.")
            self.mark_manual("Restore abortado: arquivo de backup corrompido.")
            return

        apps = manifest.get("apps", [])
        self.ctx.logger.write(
            f"{badge('backup', Color.INFO)} {archive.name} - criado em {manifest.get('created', '?')} "
            f"em {manifest.get('host', '?')}"
        )
        chosen = self._choose_apps_to_restore(apps)
        if not chosen:
            announce(self.ctx.logger, "skipped", "Nenhum app marcado; nada foi restaurado.")
            self.mark_skipped("Restore cancelado (nenhum app marcado).")
            return

        relpaths: list[str] = []
        for item in chosen:
            relpaths.extend(item.get("paths", []))
        if not relpaths:
            self.mark_skipped("Restore cancelado: os apps marcados nao tem caminhos.")
            return

        self.ctx.logger.write(paint(f"Restaurando: {', '.join(item.get('app', '?') for item in chosen)}", Color.MUTED))
        if not confirm_phrase("restaurar", self.ctx.logger):
            announce(self.ctx.logger, "skipped", "Confirmacao nao recebida; restore cancelado.")
            self.mark_skipped("Restore cancelado pelo usuario.")
            return

        self._safety_backup()
        self.ctx.runner.run(
            ["tar", "-xzf", str(plain), f"--exclude={MANIFEST_NAME}", "-C", str(self.ctx.user.home), *relpaths],
            action=f"Restaurando configuracoes de {archive.name}",
        )
        announce(self.ctx.logger, "done", "Configuracoes restauradas.")
        self.add_hint("Feche e reabra os apps afetados para carregarem as configuracoes restauradas.")
        self.mark_done(f"Configuracoes restauradas a partir de {archive.name}.")

    def _choose_apps_to_restore(self, apps: list[dict]) -> list[dict]:
        if not apps:
            return []
        if len(apps) == 1:
            return apps
        indices = select_many(
            "Quais apps restaurar?",
            [item.get("app", "?") for item in apps],
            self.ctx.logger,
            detail="Desmarque o que nao quer sobrescrever.",
            preselected=list(range(len(apps))),
        )
        return [apps[i] for i in indices]

    def _safety_backup(self) -> None:
        """Snapshot LOCAL das configs atuais antes de sobrescrever (rollback)."""
        present = self._present_entries()
        if not present:
            return
        stamp = time.strftime("%Y%m%d-%H%M%S")
        safety_dir = self._safety_dir()
        safety = safety_dir / f"pre-restore-{stamp}{PLAIN_SUFFIX}"
        self._create_plain_archive(safety, present, encrypted=False)
        announce(self.ctx.logger, "done", f"Copia de seguranca das configs atuais em {safety}")
        self._prune_dir(safety_dir, ("pre-restore-*",), KEEP_SAFETY)
