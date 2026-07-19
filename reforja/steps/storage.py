from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, replace
from pathlib import Path

from ..core import (
    Color,
    announce,
    backup_existing,
    badge,
    capture,
    command_exists,
    confirm_phrase,
    ensure_owner,
    load_env_file,
    paint,
    prompt_user,
    select_many,
    write_text,
    write_text_sudo,
)
from ..platform import (
    install_system_package,
)
from ..steps_base import Step, StepTask
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

    def tasks(self) -> list[StepTask]:
        return [
            StepTask(
                key="rclone",
                label="Instalar o rclone",
                description="Instala o rclone, a ferramenta que fala com o Google Drive.",
                detect=lambda: command_exists("rclone"),
                run=lambda: install_system_package("rclone", self.ctx.runner),
            ),
            StepTask(
                key="remote",
                label="Conectar a sua conta do Google Drive",
                short_description="Autoriza a conta e cria o remote do Drive",
                description=(
                    "Abre o rclone config para autorizar a conta no navegador e cria o remote "
                    "'Google Drive:'. Se o token ja existir mas estiver expirado, faz a reconexao."
                ),
                detect=self._remote_ready,
                run=self._configure_remote,
            ),
            StepTask(
                key="servico",
                label="Montar o Drive em ~/GoogleDrive automaticamente",
                short_description="Servico systemd que monta o Drive",
                description=(
                    "Cria um servico systemd de usuario que monta o Google Drive em ~/GoogleDrive e o "
                    "remonta sozinho se cair, com notificacao na area de trabalho em caso de falha."
                ),
                detect=self._service_ready,
                run=self._install_service,
            ),
            StepTask(
                key="linger",
                label="Manter a montagem ativa no boot (linger)",
                short_description="Habilita o linger para subir no boot",
                description=(
                    "Habilita o linger da sessao: sem isso o servico so sobe depois que voce faz login "
                    "no ambiente grafico."
                ),
                detect=lambda: Path(f"/var/lib/systemd/linger/{self.ctx.user.name}").exists(),
                run=self._enable_linger,
            ),
        ]

    def apply(self) -> None:
        header(self, self.title, "Montando sincronizacao automatica do Google Drive")
        super().apply()
        mount_dir = self.ctx.user.home / "GoogleDrive"
        if not self.ctx.runner.dry_run and self._service_ready() and not mount_dir.is_mount():
            time.sleep(3)
            if not mount_dir.is_mount():
                self.ctx.logger.write(
                    f"{Color.WARNING}Aviso: {mount_dir} ainda nao esta montado. "
                    f"Verifique com: systemctl --user status rclone-google-drive.service{Color.RESET}"
                )
                self.add_hint("Se a montagem nao aparecer, rode: rclone config reconnect 'Google Drive:'")

    def _remote_ready(self) -> bool:
        # Leitura pura via capture: detecta corretamente mesmo na sondagem do card
        # (Runner em dry-run devolveria None e falsearia como "nao configurado").
        rclone_env = self._rclone_env()
        if not command_exists("rclone"):
            return False
        remotes = capture(["rclone", "listremotes"], env_extra=rclone_env, timeout=8)
        if self.remote not in (remotes.stdout or ""):
            return False
        # Timeout curto: valida o token sem pendurar a sondagem do card se a rede cair.
        token = capture(
            ["rclone", "lsd", self.remote, "--max-depth", "0", "--contimeout", "6s", "--timeout", "8s"],
            env_extra=rclone_env,
            timeout=12,
        )
        return token.returncode == 0

    def _configure_remote(self) -> None:
        rclone_env = self._rclone_env()
        remotes = self.ctx.runner.run(
            ["rclone", "listremotes"],
            check=False,
            action="Verificando remotes do rclone",
            show_progress=False,
            env_extra=rclone_env,
        )
        if remotes and self.remote not in (remotes.stdout or ""):
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
            if not remotes_after or self.remote not in (remotes_after.stdout or ""):
                self.mark_manual("Remote do Google Drive ainda nao foi configurado.")
                return
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

    def _enable_linger(self) -> None:
        self.ctx.runner.run(
            ["loginctl", "enable-linger", self.ctx.user.name],
            check=False,
            action="Habilitando persistencia de sessao para servicos ao boot",
        )

    @property
    def _service_content(self) -> str:
        return """[Unit]
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

    @property
    def _notify_content(self) -> str:
        return """\
[Unit]
Description=Notificacao de falha na montagem do Google Drive

[Service]
Type=oneshot
ExecStart=/usr/bin/notify-send -u critical -i dialog-error "Google Drive nao montado" "Token pode ter expirado. Para corrigir, execute:\\npython -m reforja step 07 apply"
"""

    @property
    def _service_dir(self) -> Path:
        return self.ctx.user.home / ".config/systemd/user"

    def _service_ready(self) -> bool:
        service_file = self._service_dir / "rclone-google-drive.service"
        notify_file = self._service_dir / "rclone-google-drive-notify.service"
        files_current = (
            service_file.exists()
            and service_file.read_text(encoding="utf-8", errors="ignore") == self._service_content
            and notify_file.exists()
            and notify_file.read_text(encoding="utf-8", errors="ignore") == self._notify_content
        )
        if not files_current:
            return False
        return self._user_service_enabled("rclone-google-drive.service") and self._user_service_active(
            "rclone-google-drive.service"
        )

    def _install_service(self) -> None:
        mount_dir = self.ctx.user.home / "GoogleDrive"
        service_file = self._service_dir / "rclone-google-drive.service"
        notify_file = self._service_dir / "rclone-google-drive-notify.service"
        if not self.ctx.runner.dry_run:
            mount_dir.mkdir(parents=True, exist_ok=True)
            self._service_dir.mkdir(parents=True, exist_ok=True)
        write_text(service_file, self._service_content, self.ctx.runner)
        write_text(notify_file, self._notify_content, self.ctx.runner)
        self.ctx.runner.run(
            ["systemctl", "--user", "daemon-reload"],
            check=False,
            action="Recarregando servicos do usuario",
            show_progress=False,
        )
        self.ctx.runner.run(
            ["systemctl", "--user", "enable", "--now", "rclone-google-drive.service"],
            check=False,
            action="Habilitando montagem automatica do Google Drive",
        )

    def _user_service_active(self, name: str) -> bool:
        # capture (nao Runner): leitura que precisa rodar mesmo em dry-run (sondagem).
        return capture(["systemctl", "--user", "is-active", "--quiet", name]).returncode == 0

    def _user_service_enabled(self, name: str) -> bool:
        return capture(["systemctl", "--user", "is-enabled", "--quiet", name]).returncode == 0

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
            self.ctx.logger.write(f"{badge('dry-run', Color.DRY_RUN)} removeria {service_file}")
            self.ctx.logger.write(f"{badge('dry-run', Color.DRY_RUN)} removeria {notify_service_file}")
        else:
            service_file.unlink(missing_ok=True)
            notify_service_file.unlink(missing_ok=True)


@dataclass(frozen=True)
class Partition:
    """Uma particao candidata a ser montada, como vista pelo lsblk."""

    path: str
    size: str
    fstype: str
    label: str
    uuid: str
    mountpoint: str
    removable: bool
    model: str
    parttype: str = ""

    @property
    def is_ntfs(self) -> bool:
        return self.fstype.startswith("ntfs")

    @property
    def is_ext(self) -> bool:
        return self.fstype in ("ext2", "ext3", "ext4")

    @property
    def no_posix_owner(self) -> bool:
        """Sistemas de arquivos sem dono/permissao POSIX (Windows).

        Eles montam como root a menos que a propria linha do fstab diga de quem
        sao os arquivos, via uid/gid/umask.
        """
        return self.is_ntfs or self.fstype in ("vfat", "exfat", "msdos")

    @property
    def is_udisks_mount(self) -> bool:
        """Esta montado agora pelo udisks (o "clicar no Dolphin"), nao pelo fstab."""
        return self.mountpoint.startswith("/run/media/") or self.mountpoint.startswith("/media/")


class FstabStep(Step):
    id = "08"
    title = "Montagem de discos / fstab"
    description = (
        "Le os discos conectados na maquina, deixa voce escolher quais montar no boot e em qual pasta, "
        "e grava o bloco no /etc/fstab (com backup e confirmacao digitada). Eles passam a montar sozinhos "
        "no boot, gravaveis pelo seu usuario e sem pedir senha, em vez de depender de um clique no Dolphin. "
        "Discos externos usam automount com nofail: o boot nunca espera nem quebra quando eles estao "
        "desconectados."
    )
    begin = "# BEGIN pos-formatacao-cachyos"
    end = "# END pos-formatacao-cachyos"
    fstab_path = Path("/etc/fstab")

    # Sistemas de arquivos que nunca sao candidatos a montagem em /mnt.
    skip_fstypes = ("swap", "crypto_LUKS", "LVM2_member", "linux_raid_member", "squashfs")
    # Particoes de servico (EFI, reservada, recuperacao): nao sao dados do usuario.
    skip_parttypes = (
        "EFI System",
        "Microsoft reserved",
        "Windows recovery environment",
        "BIOS boot",
        "Linux swap",
        "Linux extended boot",
    )
    # Pontos de montagem do proprio sistema: se a particao ja esta ai, nao mexemos.
    system_mounts = ("/", "/boot", "/home", "/root", "/srv", "/usr", "/var", "/efi", "[SWAP]")

    # O veredito vem da conferencia do bloco gerenciado do /etc/fstab.
    compliance_from_plan = False

    # --- fluxo principal ---------------------------------------------------------

    def tasks(self) -> list[StepTask]:
        return [
            StepTask(
                key="fstab",
                label="Configurar a montagem automatica dos discos",
                short_description="Monta discos no boot via /etc/fstab",
                description=(
                    "Sonda os discos com lsblk, deixa voce marcar quais montar no boot (ja vem marcado o "
                    "que hoje esta no bloco gerenciado do /etc/fstab), permite trocar o ponto de montagem "
                    "de cada um e so grava depois de mostrar o preview e voce digitar APLICAR-FSTAB. "
                    "Faz backup do /etc/fstab antes e reverte sozinho se a validacao falhar."
                ),
                detect=self._fstab_block_ready,
                run=self._configure_fstab,
            )
        ]

    def apply(self) -> None:
        super().apply()

    def _fstab_block_ready(self) -> str | bool:
        entries = self._managed_entries()
        if not entries:
            return False
        return f"{len(entries)} ponto(s) de montagem no fstab"

    def _configure_fstab(self) -> None:
        header(self, self.title, "Escolha o que montar automaticamente no boot")
        partitions = self._candidates()
        if not partitions:
            self.ctx.logger.write(
                f"{badge('aviso', Color.WARNING)} Nenhuma particao candidata foi detectada "
                f"(so aparecem discos com sistema de arquivos e que nao sejam do sistema)."
            )
            self.mark_skipped("Nenhuma particao candidata detectada; /etc/fstab nao foi alterado.")
            self.mark_attention(
                "Nenhuma particao candidata detectada; nada foi gravado no fstab.",
                attention=["nenhum disco candidato encontrado"],
            )
            return

        existing = self._managed_entries()
        selection = self._review_mountpoints(self._select_partitions(partitions, existing))

        lines = self._build_lines(selection)
        self._preview(lines)

        if not selection and existing:
            self.ctx.logger.write(
                f"{badge('aviso', Color.WARNING)} Nada selecionado: o bloco atual do fstab sera esvaziado."
            )
        if not self.ctx.runner.dry_run and not confirm_phrase("APLICAR-FSTAB", self.ctx.logger):
            self.mark_skipped("Confirmacao nao conferiu; /etc/fstab nao foi alterado.")
            return

        current = self.fstab_path.read_text(encoding="utf-8")
        content = self._with_block(current, lines)
        if current == content:
            self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} /etc/fstab ja contem o bloco esperado")
            self._settle(existing, selection, lines)
            self.mark_skipped("/etc/fstab ja continha o bloco esperado.")
            return

        backup = backup_existing(self.fstab_path, self.ctx.runner, sudo=True)
        self._ensure_mountpoints(selection)
        write_text_sudo(self.fstab_path, content, self.ctx.runner)
        if not self._verify_fstab():
            self._restore(backup)
            self.mark_attention(
                "O fstab gerado nao passou na verificacao; o backup foi restaurado.",
                attention=["fstab invalido (findmnt --verify falhou)"],
            )
            return
        self.ctx.runner.run(
            ["systemctl", "daemon-reload"],
            sudo=True,
            action="Recarregando systemd apos ajuste do fstab",
            show_progress=False,
        )
        self._settle(existing, selection, lines)
        self.mark_done(f"{len(lines)} montagem(ns) gravada(s) no /etc/fstab.")

    def _settle(self, existing: dict[str, str], selection: list[Partition], lines: list[str]) -> None:
        """Poe o sistema no estado que o fstab acabou de descrever.

        Ordem importa: primeiro soltamos o que o udisks montou (senao o `mount -a`
        nao consegue montar o mesmo disco em /mnt), depois montamos, depois
        acertamos o dono e por fim removemos os pontos de montagem que sairam do
        bloco.
        """
        self._ensure_mountpoints(selection)
        self._release_udisks_mounts(selection)
        problems = self._mount_all(selection)
        self._fix_ownership(selection)
        self._cleanup_stale_mountpoints(existing, selection)
        if problems:
            self.mark_attention(
                f"{len(lines)} montagem(ns) no /etc/fstab, mas {len(problems)} com pendencia.",
                attention=problems,
            )
            return
        self.mark_applied(
            f"{len(lines)} montagem(ns) configurada(s) no /etc/fstab.",
            items=[f"{item.mountpoint} ({item.path})" for item in selection],
        )

    # --- sondagem ----------------------------------------------------------------

    def _probe_partitions(self) -> list[Partition]:
        """Le os discos conectados via lsblk.

        Usa `capture` (e nao o Runner) porque isto e leitura pura e precisa
        funcionar tambem em dry-run e SEM sudo: o `blkid -s UUID -o value <dev>`
        que este step usava antes devolvia vazio sem root, o que fazia o bloco do
        fstab sair vazio em silencio.
        """
        result = capture(
            [
                "lsblk",
                "-J",
                "-o",
                "PATH,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINT,TYPE,HOTPLUG,RM,TRAN,MODEL,PARTTYPENAME",
            ]
        )
        if result.returncode != 0 or not result.stdout.strip():
            self.ctx.logger.write(f"{badge('aviso', Color.WARNING)} Nao consegui listar os discos com lsblk")
            return []
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            self.ctx.logger.write(f"{badge('aviso', Color.WARNING)} Saida do lsblk em formato inesperado")
            return []

        partitions: list[Partition] = []

        def walk(node: dict, parent: dict | None) -> None:
            inherited = parent or {}
            tran = (node.get("tran") or inherited.get("tran") or "").lower()
            removable = bool(
                node.get("hotplug")
                or node.get("rm")
                or inherited.get("hotplug")
                or inherited.get("rm")
                or tran in ("usb", "ieee1394")
            )
            if node.get("type") in ("part", "disk") and node.get("fstype") and node.get("uuid"):
                partitions.append(
                    Partition(
                        path=node.get("path") or "",
                        size=node.get("size") or "?",
                        fstype=node.get("fstype") or "",
                        label=node.get("label") or "",
                        uuid=node.get("uuid") or "",
                        mountpoint=node.get("mountpoint") or "",
                        removable=removable,
                        model=(node.get("model") or inherited.get("model") or "").strip(),
                        parttype=(node.get("parttypename") or "").strip(),
                    )
                )
            for child in node.get("children") or []:
                walk(child, {**inherited, **node})

        for device in payload.get("blockdevices") or []:
            walk(device, None)
        return partitions

    def _candidates(self) -> list[Partition]:
        """Particoes que fazem sentido oferecer: descarta as do sistema e as que
        ja estao no fstab fora do nosso bloco (escritas pelo instalador)."""
        foreign = self._foreign_uuids()
        candidates: list[Partition] = []
        for part in self._probe_partitions():
            if part.fstype in self.skip_fstypes:
                continue
            if part.parttype in self.skip_parttypes:
                continue
            if part.uuid in foreign:
                continue
            if part.mountpoint and self._is_system_mount(part.mountpoint):
                continue
            candidates.append(part)
        return candidates

    def _is_system_mount(self, mountpoint: str) -> bool:
        for mount in self.system_mounts:
            if mountpoint == mount:
                return True
            # "/" nao propaga: senao qualquer caminho seria "do sistema".
            if mount != "/" and mountpoint.startswith(f"{mount.rstrip('/')}/"):
                return True
        return False

    def _fstab_text(self) -> str:
        try:
            return self.fstab_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""

    def _foreign_uuids(self) -> set[str]:
        """UUIDs ja declarados no fstab FORA do nosso bloco (raiz, /boot, swap...)."""
        outside = self._without_block(self._fstab_text())
        return set(re.findall(r"UUID=(\S+)", outside))

    def _managed_entries(self) -> dict[str, str]:
        """UUID -> ponto de montagem, para o bloco que este step gerencia."""
        match = re.search(rf"{re.escape(self.begin)}(.*?){re.escape(self.end)}", self._fstab_text(), flags=re.S)
        if not match:
            return {}
        entries: dict[str, str] = {}
        for line in match.group(1).splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[0].startswith("UUID="):
                entries[fields[0][len("UUID=") :]] = fields[1]
        return entries

    # --- interacao ---------------------------------------------------------------

    def _select_partitions(self, partitions: list[Partition], existing: dict[str, str]) -> list[Partition]:
        options = [self._describe(part, existing) for part in partitions]
        if existing:
            preselected = [index for index, part in enumerate(partitions) if part.uuid in existing]
        else:
            # Primeira execucao: marca tudo. `_candidates()` ja tirou o que e do
            # sistema (raiz, boot, swap, ESP, recuperacao), entao o que sobrou e
            # disco de dados — e o normal e querer todos montados no boot.
            preselected = list(range(len(partitions)))
        chosen = select_many(
            "Quais discos montar automaticamente no boot?",
            options,
            self.ctx.logger,
            detail="Marcados = ja no fstab. Nada marcado = remove todas as montagens gerenciadas.",
            preselected=preselected,
        )
        selection = [partitions[index] for index in chosen]
        for part in selection:
            self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} {part.path} -> {self._mountpoint(part, existing)}")
        # O mountpoint escolhido antes (se houver) prevalece sobre a sugestao.
        return [replace(part, mountpoint=self._mountpoint(part, existing)) for part in selection]

    def _describe(self, part: Partition, existing: dict[str, str]) -> str:
        pieces = [f"{part.path}", part.size, part.fstype or "?"]
        if part.label:
            pieces.append(f'"{part.label}"')
        elif part.model:
            pieces.append(f"({part.model})")
        if part.removable:
            pieces.append("[externo]")
        pieces.append(f"-> {self._mountpoint(part, existing)}")
        if part.uuid in existing:
            pieces.append("(ja no fstab)")
        return "  ".join(pieces)

    def _review_mountpoints(self, selection: list[Partition]) -> list[Partition]:
        if not selection:
            return selection
        options = [f"{part.path} -> {part.mountpoint}" for part in selection]
        chosen = select_many(
            "Quer trocar algum ponto de montagem? (nada marcado = aceita os sugeridos)",
            options,
            self.ctx.logger,
            detail="Marque so os que quiser digitar um caminho diferente.",
        )
        reviewed = list(selection)
        for index in chosen:
            part = reviewed[index]
            answer = prompt_user(
                f"Ponto de montagem para {part.path} ({part.label or part.fstype})",
                self.ctx.logger,
                detail=f"Sugerido: {part.mountpoint}. Precisa ser um caminho absoluto em /mnt ou /media.",
                prompt_label="Caminho",
                allow_empty=True,
            ).strip()
            if not answer:
                continue
            if not self._valid_mountpoint(answer):
                self.ctx.logger.write(
                    f"{badge('aviso', Color.WARNING)} Caminho invalido: {answer}. Mantendo {part.mountpoint}."
                )
                continue
            reviewed[index] = replace(part, mountpoint=answer.rstrip("/"))
        return reviewed

    def _valid_mountpoint(self, path: str) -> bool:
        if not path.startswith("/"):
            return False
        if self._is_system_mount(path.rstrip("/") or "/"):
            return False
        return path.startswith("/mnt/") or path.startswith("/media/")

    def _preview(self, lines: list[str]) -> None:
        self.ctx.logger.write(f"{badge('info', Color.INFO)} Bloco que sera gravado no /etc/fstab:")
        self.ctx.logger.write(paint(self.begin, Color.MUTED))
        for line in lines or ["# (vazio: nenhuma montagem gerenciada)"]:
            self.ctx.logger.write(paint(line, Color.INFO))
        self.ctx.logger.write(paint(self.end, Color.MUTED))

    # --- geracao das linhas ------------------------------------------------------

    def _mountpoint(self, part: Partition, existing: dict[str, str]) -> str:
        if part.uuid in existing:
            return existing[part.uuid]
        base = part.label or part.model or part.uuid[:8]
        return f"/mnt/{self._slug(base)}"

    @staticmethod
    def _slug(value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
        return slug or "disco"

    def _build_lines(self, selection: list[Partition]) -> list[str]:
        lines: list[str] = []
        for part in selection:
            opts, passno = self._options_for(part)
            lines.append(f"UUID={part.uuid} {part.mountpoint} {part.fstype} {opts} 0 {passno}")
        return lines

    def _options_for(self, part: Partition) -> tuple[str, str]:
        opts = ["rw" if part.no_posix_owner else "defaults"]
        if part.removable:
            # noauto + automount: o boot nao espera nem falha sem o disco; ele monta
            # sozinho no primeiro acesso a pasta e desmonta quando fica ocioso.
            opts += ["noauto", "x-systemd.automount", "x-systemd.idle-timeout=60"]
        opts += ["nofail", "x-systemd.device-timeout=5s"]
        # `users` deixa o usuario montar/desmontar sem sudo (e o que evita o pedido de
        # senha do polkit ao clicar no disco no Dolphin); `x-gvfs-show` faz a montagem
        # aparecer na barra lateral. Atencao: `users` implica noexec, entao o `exec`
        # precisa vir DEPOIS dele, ou nada roda a partir do disco (jogos, AppImages).
        opts += ["users", "exec", "x-gvfs-show", "noatime"]
        if part.no_posix_owner:
            # Sem dono POSIX no FS, quem define de quem sao os arquivos e o fstab.
            opts += [f"uid={self.ctx.user.uid}", f"gid={self.ctx.user.gid}", "umask=022"]
            if part.is_ntfs:
                opts.append("windows_names")
        if part.is_ext:
            # commit= so existe nos ext2/3/4; em outros FS o mount recusaria a opcao.
            opts.append("commit=60")
        # Sem fsck no boot para disco que pode nao estar presente.
        passno = "2" if part.is_ext and not part.removable else "0"
        return ",".join(opts), passno

    # --- escrita -----------------------------------------------------------------

    def _with_block(self, current: str, lines: list[str]) -> str:
        cleaned = self._without_block(current)
        return cleaned.rstrip() + "\n\n" + "\n".join([self.begin, *lines, self.end]) + "\n"

    def _without_block(self, text: str) -> str:
        return re.sub(rf"\n?{re.escape(self.begin)}.*?{re.escape(self.end)}\n?", "\n", text, flags=re.S)

    def _ensure_mountpoints(self, selection: list[Partition]) -> None:
        for part in selection:
            if Path(part.mountpoint).exists():
                self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} {part.mountpoint} ja existe")
            else:
                self.ctx.runner.run(["mkdir", "-p", part.mountpoint], sudo=True)

    def _verify_fstab(self) -> bool:
        result = self.ctx.runner.run(
            ["findmnt", "--verify"],
            sudo=True,
            check=False,
            action="Validando o /etc/fstab",
            show_progress=False,
        )
        if result is None:  # dry-run
            return True
        return result.returncode == 0

    def _restore(self, backup: Path | None) -> None:
        if backup is None:
            self.ctx.logger.write(f"{badge('erro', Color.ERROR)} Sem backup para restaurar; revise o /etc/fstab a mao")
            return
        self.ctx.runner.run(["cp", "-a", str(backup), str(self.fstab_path)], sudo=True, check=False)
        self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} /etc/fstab restaurado a partir de {backup}")

    def _release_udisks_mounts(self, selection: list[Partition]) -> None:
        """Desmonta o que o udisks (Dolphin) montou em /run/media ou /media.

        Enquanto o disco esta montado la, o `mount -a` nao consegue monta-lo em
        /mnt: o ntfs-3g recusa abrir um volume ja aberto e o ext4 acabaria com o
        mesmo disco em dois lugares. Sonda de novo em vez de usar o mountpoint da
        selecao, que a essa altura ja e o caminho de DESTINO.
        """
        targets = {part.uuid: part.mountpoint for part in selection}
        for part in self._probe_partitions():
            if part.uuid not in targets or not part.is_udisks_mount:
                continue
            if part.mountpoint == targets[part.uuid]:
                continue
            self.ctx.logger.write(
                f"{badge('info', Color.INFO)} {part.path} esta montado em {part.mountpoint} "
                f"(pelo Dolphin); soltando para montar em {targets[part.uuid]}"
            )
            # udisksctl roda como usuario: a sessao e dona da montagem, nao pede senha.
            result = self.ctx.runner.run(["udisksctl", "unmount", "-b", part.path], check=False)
            if result is not None and result.returncode != 0:
                self.ctx.runner.run(["umount", part.path], sudo=True, check=False)
            if not self.ctx.runner.dry_run and self._is_mounted(part.mountpoint):
                self.add_hint(
                    f"{part.path} continua montado em {part.mountpoint}: feche o Dolphin (ou o que estiver "
                    f"usando o disco) e rode `sudo mount -a`."
                )

    def _mount_all(self, selection: list[Partition]) -> list[str]:
        """Monta o bloco e confere disco a disco. Devolve as pendencias."""
        self.ctx.runner.run(["mount", "-a"], sudo=True, check=False, action="Aplicando montagens do fstab")
        if self.ctx.runner.dry_run:
            return []
        problems: list[str] = []
        for part in selection:
            if part.removable:
                # Com x-systemd.automount o disco so monta no primeiro acesso a pasta.
                continue
            options = self._mount_options(part.mountpoint)
            if options is None:
                problems.append(f"{part.mountpoint}: nao montou")
                self.ctx.logger.write(f"{badge('erro', Color.ERROR)} {part.mountpoint}: nao montou")
                self._hint_ntfs(part)
                continue
            if "ro" in options.split(","):
                problems.append(f"{part.mountpoint}: montou somente leitura")
                self.ctx.logger.write(f"{badge('aviso', Color.WARNING)} {part.mountpoint}: montou somente leitura")
                self._hint_ntfs(part)
                continue
            self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} {part.mountpoint}: montado")
        return problems

    def _hint_ntfs(self, part: Partition) -> None:
        if not part.is_ntfs:
            self.add_hint(f"Veja o log do `mount -a` e tente `sudo mount {part.mountpoint}` a mao.")
            return
        # Volume NTFS "sujo" (hibernado ou fechado pela Inicializacao Rapida do
        # Windows) monta somente leitura ou nem monta.
        self.add_hint(
            f"{part.path} e NTFS: desligue a Inicializacao Rapida no Windows (e nao o deixe hibernado), "
            f"depois rode `sudo ntfsfix -d {part.path}` e `sudo mount -a`."
        )

    def _mount_options(self, mountpoint: str) -> str | None:
        """Opcoes com que o ponto esta montado agora, ou None se nao esta montado."""
        result = capture(["findmnt", "-n", "-o", "OPTIONS", "--mountpoint", mountpoint])
        if result.returncode != 0:
            return None
        return result.stdout.strip()

    def _fix_ownership(self, selection: list[Partition]) -> None:
        """Passa a raiz dos discos com dono POSIX para quem esta rodando o script.

        ext4/btrfs/xfs guardam o dono no proprio disco e montam como root:root; sem
        isso o usuario precisa de sudo para escrever no proprio disco de dados. Nos
        FS do Windows quem resolve isso e o uid=/gid= da linha do fstab.
        """
        if self.ctx.runner.dry_run:
            return
        candidates: list[Partition] = []
        for part in selection:
            # Removivel usa automount: um stat() aqui montaria o disco so pra olhar.
            if part.removable or part.no_posix_owner or not self._is_mounted(part.mountpoint):
                continue
            try:
                if Path(part.mountpoint).stat().st_uid != self.ctx.user.uid:
                    candidates.append(part)
            except OSError:
                continue
        if not candidates:
            return
        chosen = select_many(
            f"Passar a raiz destes discos para o usuario {self.ctx.user.name}?",
            [f"{part.mountpoint} ({part.path}, {part.fstype})" for part in candidates],
            self.ctx.logger,
            detail="Eles montam como root; sem isso voce precisa de sudo para escrever neles.",
            preselected=list(range(len(candidates))),
        )
        for index in chosen:
            part = candidates[index]
            # Sem -R: mexer so na raiz da montagem, nao em centenas de GB de arquivos.
            ensure_owner(Path(part.mountpoint), self.ctx.user, self.ctx.runner)
            self.ctx.logger.write(
                f"{badge('ok', Color.SUCCESS)} {part.mountpoint} agora e de {self.ctx.user.name} "
                f"(as pastas de dentro mantem o dono que ja tinham)"
            )

    def _cleanup_stale_mountpoints(self, existing: dict[str, str], selection: list[Partition]) -> None:
        """Remove pontos de montagem que sairam do bloco (e ficaram vazios em /mnt)."""
        keep = {part.mountpoint for part in selection}
        self._remove_mountpoints([mountpoint for mountpoint in existing.values() if mountpoint not in keep])

    def _remove_mountpoints(self, mountpoints: list[str]) -> None:
        for mountpoint in mountpoints:
            # Cinto de seguranca: so mexemos no que este step poderia ter criado.
            if not self._valid_mountpoint(mountpoint):
                continue
            self.ctx.runner.run(["umount", mountpoint], sudo=True, check=False)
            # rmdir (nunca rm -rf): se sobrou qualquer coisa la dentro, ele falha e
            # a pasta fica — melhor uma pasta orfa do que apagar dados por engano.
            self.ctx.runner.run(
                ["rmdir", mountpoint],
                sudo=True,
                check=False,
                action=f"Removendo ponto de montagem {mountpoint}",
                show_progress=False,
            )

    # --- status / undo -----------------------------------------------------------

    def status(self) -> None:
        header(self, self.title)
        entries = self._managed_entries()
        text = self._fstab_text()
        if self.begin not in text or self.end not in text:
            self.mark_pending(
                "Bloco de montagem ainda nao esta presente no /etc/fstab.",
                missing=["bloco de montagem no fstab"],
            )
            return
        if not entries:
            # Exatamente o caso do bug antigo: bloco presente, porem vazio.
            self.ctx.logger.write(
                f"{badge('aviso', Color.WARNING)} O bloco existe no /etc/fstab mas esta VAZIO: "
                f"nenhum disco esta sendo montado no boot."
            )
            self.mark_pending(
                "Bloco presente no /etc/fstab, porem sem nenhuma montagem.",
                missing=["nenhuma montagem no bloco do fstab"],
            )
            return

        present = {part.uuid for part in self._probe_partitions()}
        applied: list[str] = []
        attention: list[str] = []
        for uuid, mountpoint in entries.items():
            if not self._is_mounted(mountpoint):
                if uuid in present:
                    attention.append(f"{mountpoint}: no fstab, disco presente, mas nao montado")
                    self.ctx.logger.write(f"{badge('aviso', Color.WARNING)} {mountpoint}: no fstab e nao montado")
                else:
                    # Disco externo desconectado e o comportamento esperado.
                    applied.append(f"{mountpoint}: no fstab (disco desconectado agora)")
                    self.ctx.logger.write(f"{badge('info', Color.INFO)} {mountpoint}: disco desconectado no momento")
                continue
            applied.append(f"{mountpoint}: montado")
            self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} {mountpoint}: montado")

        if attention:
            self.mark_attention(f"{len(entries)} montagem(ns) no fstab, com pendencias.", attention=attention)
        else:
            self.mark_applied(f"{len(entries)} montagem(ns) configurada(s) no /etc/fstab.", items=applied)

    def _is_mounted(self, mountpoint: str) -> bool:
        # Nao dava pra combinar --target com --mountpoint: o findmnt recusa as duas
        # juntas e devolvia rc=1 sempre, ou seja, TODO ponto parecia desmontado.
        return self._mount_options(mountpoint) is not None

    def undo(self) -> None:
        header(self, self.title, "Removendo as montagens gerenciadas")
        if not self.ctx.runner.dry_run and not confirm_phrase("REMOVER-FSTAB", self.ctx.logger):
            self.mark_skipped("Confirmacao nao conferiu; /etc/fstab nao foi alterado.")
            return
        entries = self._managed_entries()
        backup_existing(self.fstab_path, self.ctx.runner, sudo=True)
        write_text_sudo(self.fstab_path, self._without_block(self._fstab_text()), self.ctx.runner)
        self._remove_mountpoints(list(entries.values()))
        self.ctx.runner.run(
            ["systemctl", "daemon-reload"],
            sudo=True,
            action="Recarregando systemd apos remocao do bloco fstab",
            show_progress=False,
        )
        self.mark_done("Bloco de montagem removido do /etc/fstab.")
