from __future__ import annotations

import re
import time
from pathlib import Path

from ..core import (
    Color,
    announce,
    backup_existing,
    confirm_phrase,
    load_env_file,
    write_text,
    write_text_sudo,
)
from ..installers import (
    install_system_package,
)
from ..steps_base import Step
from ._common import header


class RcloneStep(Step):
    id = "07"
    title = "Google Drive / rclone"
    description = (
        "Instala e configura o rclone e um servico systemd de usuario que monta o Google Drive "
        "em ~/GoogleDrive automaticamente."
    )
    remote = "Google Drive:"

    def _rclone_env(self) -> dict[str, str]:
        env_file = self.ctx.root / ".env.local"
        loaded = load_env_file(env_file)
        env: dict[str, str] = {}
        client_id = loaded.get("ID_DO_CLIENTE", "").strip()
        client_secret = loaded.get("CHAVE_SECRETA_DO_CLIENTE", "").strip()
        if client_id and client_secret:
            env["RCLONE_CONFIG_GOOGLE_DRIVE_CLIENT_ID"] = client_id
            env["RCLONE_CONFIG_GOOGLE_DRIVE_CLIENT_SECRET"] = client_secret
        elif loaded:
            announce(
                self.ctx.logger,
                "warning",
                ".env.local encontrado, mas faltam ID_DO_CLIENTE ou CHAVE_SECRETA_DO_CLIENTE para o rclone.",
            )
        return env

    def apply(self) -> None:
        header(self, self.title, "Montando sincronizacao automatica do Google Drive")
        install_system_package("rclone", self.ctx.runner)
        mount_dir = self.ctx.user.home / "GoogleDrive"
        service_dir = self.ctx.user.home / ".config/systemd/user"
        service_file = service_dir / "rclone-google-drive.service"
        rclone_env = self._rclone_env()
        if not self.ctx.runner.dry_run:
            mount_dir.mkdir(parents=True, exist_ok=True)
            service_dir.mkdir(parents=True, exist_ok=True)
        remotes = self.ctx.runner.run(
            ["rclone", "listremotes"],
            check=False,
            action="Verificando remotes do rclone",
            show_progress=False,
            env_extra=rclone_env,
        )
        if remotes and self.remote not in remotes.stdout:
            self.ctx.logger.write("Remote 'Google Drive:' nao encontrado. Abrindo rclone config.")
            self.ctx.runner.run(
                ["rclone", "config"],
                check=False,
                action="Abrindo configuracao interativa do rclone",
                interactive=True,
                interactive_tty=True,
                manual_message="Comando interativo: configure o remote Google Drive no rclone. Isso nao e travamento.",
                env_extra=rclone_env,
            )
            remotes_after = self.ctx.runner.run(
                ["rclone", "listremotes"],
                check=False,
                action="Revalidando remotes do rclone apos configuracao",
                show_progress=False,
                env_extra=rclone_env,
            )
            if not remotes_after or self.remote not in remotes_after.stdout:
                self.mark_manual("Remote do Google Drive ainda nao foi configurado; servico nao sera habilitado.")
                self.mark_pending("Remote do Google Drive ainda nao foi configurado.", missing=["remote Google Drive"])
                return
            self.mark_done("Remote do Google Drive configurado manualmente e pronto para ativacao.")
        token_test = self.ctx.runner.run(
            ["rclone", "lsd", self.remote, "--max-depth", "0"],
            check=False,
            action="Validando token do Google Drive",
            show_progress=False,
            env_extra=rclone_env,
        )
        if token_test and token_test.returncode != 0:
            self.ctx.logger.write(
                f"{Color.WARNING}Token do Google Drive invalido ou expirado. Abrindo reconexao...{Color.RESET}"
            )
            self.ctx.runner.run(
                ["rclone", "config", "reconnect", self.remote],
                check=False,
                action="Reconectando conta do Google Drive",
                interactive=True,
                interactive_tty=True,
                manual_message="Autorize novamente o acesso ao Google Drive no navegador que sera aberto.",
                env_extra=rclone_env,
            )
            recheck = self.ctx.runner.run(
                ["rclone", "lsd", self.remote, "--max-depth", "0"],
                check=False,
                action="Revalidando token apos reconexao",
                show_progress=False,
                env_extra=rclone_env,
            )
            if recheck and recheck.returncode != 0:
                self.mark_manual("Token ainda invalido apos reconexao. Verifique as permissoes OAuth.")
                self.mark_pending("Token do Google Drive invalido.", missing=["token valido"])
                return
        service_content = """[Unit]
Description=Rclone Google Drive mount
After=network-online.target
Wants=network-online.target
OnFailure=rclone-google-drive-notify.service
StartLimitBurst=3
StartLimitIntervalSec=120

[Service]
Type=simple
ExecStart=/usr/bin/rclone mount 'Google Drive:' %h/GoogleDrive --vfs-cache-mode writes --dir-cache-time 72h --poll-interval 15s
ExecStop=/usr/bin/fusermount3 -u %h/GoogleDrive
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
"""
        notify_service_file = service_dir / "rclone-google-drive-notify.service"
        notify_service_content = """\
[Unit]
Description=Notificacao de falha na montagem do Google Drive

[Service]
Type=oneshot
ExecStart=/usr/bin/notify-send -u critical -i dialog-error "Google Drive nao montado" "Token pode ter expirado. Para corrigir, execute:\\npython -m reforja step 07 apply"
"""
        service_was_current = (
            service_file.exists()
            and service_file.read_text(encoding="utf-8", errors="ignore") == service_content
            and notify_service_file.exists()
            and notify_service_file.read_text(encoding="utf-8", errors="ignore") == notify_service_content
        )
        write_text(service_file, service_content, self.ctx.runner)
        write_text(notify_service_file, notify_service_content, self.ctx.runner)
        if not service_was_current or self.ctx.runner.dry_run:
            self.ctx.runner.run(
                ["systemctl", "--user", "daemon-reload"],
                check=False,
                action="Recarregando servicos do usuario",
                show_progress=False,
            )
        if self._user_service_active("rclone-google-drive.service") and self._user_service_enabled(
            "rclone-google-drive.service"
        ):
            self.ctx.logger.write(
                f"{Color.GREEN}OK:{Color.RESET} rclone-google-drive.service ja esta habilitado e ativo"
            )
        else:
            self.ctx.runner.run(
                ["systemctl", "--user", "enable", "--now", "rclone-google-drive.service"],
                check=False,
                action="Habilitando montagem automatica do Google Drive",
            )
        self.ctx.runner.run(
            ["loginctl", "enable-linger", self.ctx.user.name],
            check=False,
            action="Habilitando persistencia de sessao para servicos ao boot",
        )
        if not self.ctx.runner.dry_run:
            time.sleep(3)
            if not mount_dir.is_mount():
                self.ctx.logger.write(
                    f"{Color.WARNING}Aviso: {mount_dir} ainda nao esta montado. "
                    f"Verifique com: systemctl --user status rclone-google-drive.service{Color.RESET}"
                )
                self.add_hint("Se a montagem nao aparecer, rode: rclone config reconnect 'Google Drive:'")
        if self.result.status != "manual":
            self.mark_done("Montagem automatica do Google Drive configurada.")
            self.mark_applied("Montagem automatica do Google Drive configurada e persistente no boot.")

    def _user_service_active(self, name: str) -> bool:
        result = self.ctx.runner.run(["systemctl", "--user", "is-active", "--quiet", name], check=False)
        return bool(result and result.returncode == 0)

    def _user_service_enabled(self, name: str) -> bool:
        result = self.ctx.runner.run(["systemctl", "--user", "is-enabled", "--quiet", name], check=False)
        return bool(result and result.returncode == 0)

    def status(self) -> None:
        header(self, self.title)
        rclone_env = self._rclone_env()
        self.ctx.runner.run(["rclone", "version"], check=False, env_extra=rclone_env)
        self.ctx.runner.run(["rclone", "listremotes"], check=False, env_extra=rclone_env)
        self.ctx.runner.run(["systemctl", "--user", "status", "rclone-google-drive.service", "--no-pager"], check=False)
        remote_ready = False
        remotes = self.ctx.runner.run(
            ["rclone", "listremotes"], check=False, show_progress=False, quiet_success=True, env_extra=rclone_env
        )
        if remotes and self.remote in (remotes.stdout or ""):
            remote_ready = True
        service_active = self._user_service_active("rclone-google-drive.service")
        service_enabled = self._user_service_enabled("rclone-google-drive.service")
        linger_path = Path(f"/var/lib/systemd/linger/{self.ctx.user.name}")
        linger_enabled = linger_path.exists()
        mount_point = self.ctx.user.home / "GoogleDrive"
        mount_active = mount_point.is_mount()
        if not linger_enabled:
            self.ctx.logger.write(
                f"{Color.WARNING}Aviso: linger nao habilitado — servico nao inicia no boot sem sessao ativa.{Color.RESET}"
            )
        if not mount_active:
            self.ctx.logger.write(f"{Color.WARNING}Aviso: {mount_point} NAO esta montado.{Color.RESET}")
        if remote_ready and service_active and service_enabled and mount_active and linger_enabled:
            self.mark_applied("Remote, servico e montagem do Google Drive estao ativos e persistentes no boot.")
        elif remote_ready and service_active and service_enabled and mount_active:
            self.mark_attention(
                "Montagem ativa, mas linger desabilitado — nao persiste no boot.", attention=["linger desabilitado"]
            )
        elif remote_ready:
            self.mark_attention("Remote existe, mas o servico ou a montagem precisam de atencao.")
        else:
            self.mark_pending("Remote do Google Drive ainda nao esta configurado.", missing=["remote Google Drive"])

    def undo(self) -> None:
        service_dir = self.ctx.user.home / ".config/systemd/user"
        service_file = service_dir / "rclone-google-drive.service"
        notify_service_file = service_dir / "rclone-google-drive-notify.service"
        self.ctx.runner.run(["systemctl", "--user", "disable", "--now", "rclone-google-drive.service"], check=False)
        if self.ctx.runner.dry_run:
            self.ctx.logger.write(f"{Color.YELLOW}[dry-run]{Color.RESET} removeria {service_file}")
            self.ctx.logger.write(f"{Color.YELLOW}[dry-run]{Color.RESET} removeria {notify_service_file}")
        else:
            service_file.unlink(missing_ok=True)
            notify_service_file.unlink(missing_ok=True)


class FstabStep(Step):
    id = "08"
    title = "Montagem de discos / fstab"
    description = (
        "Configura montagens de disco no /etc/fstab por label (WINDOWS, DADOS WINDOWS, JOGOS LINUX, "
        "BACKUP), com backup do arquivo e confirmacao digitada. Labels ausentes na maquina sao ignoradas."
    )
    labels = ("WINDOWS", "DADOS WINDOWS", "JOGOS LINUX", "BACKUP")
    begin = "# BEGIN pos-formatacao-cachyos"
    end = "# END pos-formatacao-cachyos"

    def apply(self) -> None:
        header(self, self.title, "Preparando montagens persistentes no boot")
        if not self.ctx.runner.dry_run and not confirm_phrase("APLICAR-FSTAB", self.ctx.logger):
            return
        lines = self._build_lines()
        fstab = Path("/etc/fstab")
        current = fstab.read_text(encoding="utf-8")
        cleaned = self._without_block(current)
        content = cleaned.rstrip() + "\n\n" + "\n".join([self.begin, *lines, self.end]) + "\n"
        if current == content:
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} /etc/fstab ja contem o bloco esperado")
            self._ensure_mountpoints()
            self.ctx.runner.run(["mount", "-a"], sudo=True, check=False, action="Aplicando montagens do fstab")
            self.mark_skipped("/etc/fstab ja continha o bloco esperado.")
            return
        backup_existing(fstab, self.ctx.runner, sudo=True)
        self._ensure_mountpoints()
        write_text_sudo(fstab, content, self.ctx.runner)
        self.ctx.runner.run(
            ["systemctl", "daemon-reload"],
            sudo=True,
            action="Recarregando systemd apos ajuste do fstab",
            show_progress=False,
        )
        self.ctx.runner.run(["mount", "-a"], sudo=True, check=False, action="Aplicando montagens do fstab")
        self.mark_done("Bloco de montagem gravado no /etc/fstab.")

    def _build_lines(self) -> list[str]:
        lines = []
        self._found_labels: list[str] = []
        for label in self.labels:
            device = self._blkid_label(label)
            if not device:
                if self.ctx.runner.dry_run:
                    self._found_labels.append(label)
                self.ctx.logger.write(f"{Color.YELLOW}AVISO:{Color.RESET} Label nao encontrado: {label}")
                continue
            self._found_labels.append(label)
            uuid = self._blkid_value(device, "UUID")
            fs = self._blkid_value(device, "TYPE")
            if not uuid or not fs:
                continue
            if fs.startswith("ntfs"):
                opts = f"rw,nofail,x-systemd.device-timeout=5,uid={self.ctx.user.uid},gid={self.ctx.user.gid},umask=022,windows_names,noatime"
                passno = "0"
            else:
                opts = "defaults,nofail,x-systemd.device-timeout=5,noatime,commit=60"
                passno = "2"
            lines.append(f"UUID={uuid} {self._mountpoint(label)} {fs} {opts} 0 {passno}")
        return lines

    def _blkid_label(self, label: str) -> str:
        result = self.ctx.runner.run(["blkid", "-L", label], check=False)
        return result.stdout.strip() if result and result.stdout else ""

    def _blkid_value(self, device: str, key: str) -> str:
        result = self.ctx.runner.run(["blkid", "-s", key, "-o", "value", device], check=False)
        return result.stdout.strip() if result and result.stdout else ""

    def _mountpoint(self, label: str) -> str:
        return {
            "WINDOWS": "/mnt/windows",
            "DADOS WINDOWS": "/mnt/dados-windows",
            "JOGOS LINUX": "/mnt/jogos-linux",
            "BACKUP": "/mnt/backup",
        }[label]

    def _ensure_mountpoints(self) -> None:
        for label in getattr(self, "_found_labels", self.labels):
            mountpoint = self._mountpoint(label)
            if Path(mountpoint).exists():
                self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} {mountpoint} ja existe")
            else:
                self.ctx.runner.run(["mkdir", "-p", mountpoint], sudo=True)

    def _without_block(self, text: str) -> str:
        return re.sub(rf"\n?{re.escape(self.begin)}.*?{re.escape(self.end)}\n?", "\n", text, flags=re.S)

    def status(self) -> None:
        header(self, self.title)
        self.ctx.runner.run(["lsblk", "-f"], check=False)
        self.ctx.runner.run(
            [
                "grep",
                "-n",
                "pos-formatacao-cachyos\\|/mnt/windows\\|/mnt/dados-windows\\|/mnt/jogos-linux\\|/mnt/backup",
                "/etc/fstab",
            ],
            check=False,
        )
        fstab = Path("/etc/fstab")
        text = fstab.read_text(encoding="utf-8", errors="ignore")
        if self.begin in text and self.end in text:
            self.mark_applied("Bloco de montagem persistente esta presente no /etc/fstab.")
        else:
            self.mark_pending(
                "Bloco esperado ainda nao esta presente no /etc/fstab.", missing=["bloco de montagem no fstab"]
            )

    def undo(self) -> None:
        if not self.ctx.runner.dry_run and not confirm_phrase("REMOVER-FSTAB", self.ctx.logger):
            return
        fstab = Path("/etc/fstab")
        backup_existing(fstab, self.ctx.runner, sudo=True)
        write_text_sudo(fstab, self._without_block(fstab.read_text(encoding="utf-8")), self.ctx.runner)
        self.ctx.runner.run(
            ["systemctl", "daemon-reload"],
            sudo=True,
            action="Recarregando systemd apos remocao do bloco fstab",
            show_progress=False,
        )
