from __future__ import annotations

import re
import shutil
from pathlib import Path

from .core import (
    Color,
    backup_existing,
    command_exists,
    confirm_phrase,
    ensure_owner,
    print_lines,
    write_text,
    write_text_sudo,
)
from .desktop import DesktopEntry, install_desktop_entry
from .installers import (
    copy_asset,
    install_flatpak,
    install_pacman,
    install_system_or_aur,
    npm_global_installed,
    pacman_exists,
    remove_flatpak,
)
from .steps_base import Step


def header(step: Step, title: str) -> None:
    step.ctx.logger.write("")
    step.ctx.logger.write(f"{Color.CYAN}{'=' * 60}{Color.RESET}")
    step.ctx.logger.write(f"{Color.CYAN}{title}{Color.RESET}")
    step.ctx.logger.write(f"{Color.CYAN}{'=' * 60}{Color.RESET}")


class ShellyStep(Step):
    id = "00"
    title = "Abrir Shelly e habilitar Flatpak, AppImage e AUR"

    def apply(self) -> None:
        header(self, self.title)
        if self._basic_support_ready():
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} Flatpak, AppImage/fuse2 e AUR helper parecem prontos. Pulando abertura do Shelly.")
            return
        self.ctx.logger.write("Habilite Flatpak, AppImage e AUR no Shelly antes de continuar.")
        if command_exists("shelly"):
            self.ctx.runner.run(["shelly"], check=False)
        elif command_exists("cachyos-hello"):
            self.ctx.runner.run(["cachyos-hello", "--startpage", "shelly"], check=False)
        else:
            self.ctx.logger.write(f"{Color.YELLOW}AVISO:{Color.RESET} Shelly/CachyOS Hello nao encontrado.")
        if not self.ctx.runner.dry_run:
            input("Depois de ajustar no Shelly, pressione ENTER para continuar...")

    def status(self) -> None:
        header(self, "Status Shelly")
        print_lines(self.ctx.logger, [
            f"shelly: {'OK' if command_exists('shelly') else 'ausente'}",
            f"cachyos-hello: {'OK' if command_exists('cachyos-hello') else 'ausente'}",
            f"flatpak: {'OK' if command_exists('flatpak') else 'ausente'}",
            f"AUR helper: {'OK' if command_exists('paru') or command_exists('yay') else 'ausente'}",
        ])

    def _basic_support_ready(self) -> bool:
        return command_exists("flatpak") and (command_exists("paru") or command_exists("yay"))


class UpdateSystemStep(Step):
    id = "01"
    title = "Atualizar sistema"

    def apply(self) -> None:
        header(self, self.title)
        install_pacman("pacman-contrib", self.ctx.runner)
        self.ctx.runner.run(["pacman", "-Syu"], sudo=True)
        self.ctx.logger.write(f"{Color.YELLOW}AVISO:{Color.RESET} Reinicie apos atualizacao grande/kernel.")

    def status(self) -> None:
        header(self, "Status sistema")
        self.ctx.runner.run(["uname", "-r"], check=False)
        self.ctx.runner.run(["pacman", "-Qu"], check=False)

    def undo(self) -> None:
        self.ctx.logger.write("Nao ha undo seguro para uma atualizacao completa. Use snapshots se estiverem configurados.")


class LinuxToysStep(Step):
    id = "00.2"
    title = "Instalar Linux Toys"

    def apply(self) -> None:
        header(self, self.title)
        if command_exists("linux-toys"):
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} Linux Toys ja parece instalado")
            return
        self.ctx.runner.run("curl -fsSL https://linux.toys/install.sh | bash", shell=True)


class AppImageSupportStep(Step):
    id = "00.3"
    title = "Suporte AppImage / fuse2"

    def apply(self) -> None:
        header(self, self.title)
        install_pacman("fuse2", self.ctx.runner)
        self.ctx.logger.write("fuse2 pronto para compatibilidade com AppImages.")

    def status(self) -> None:
        header(self, self.title)
        self.ctx.runner.run(["pacman", "-Q", "fuse2"], check=False)
        self.ctx.runner.run(["grep", "-w", "fuse", "/proc/filesystems"], check=False)


class BrowserStep(Step):
    id = "02"
    title = "Navegador e extensoes"

    def apply(self) -> None:
        header(self, "Firefox sistema + FirefoxPWA + Bitwarden")
        install_pacman("firefox", self.ctx.runner)
        if pacman_exists("firefoxpwa"):
            install_pacman("firefoxpwa", self.ctx.runner)
        else:
            install_system_or_aur("firefoxpwa", "firefoxpwa", self.ctx.runner)
        install_flatpak("com.bitwarden.desktop", self.ctx.runner)
        self.ctx.logger.write("Extensao FirefoxPWA: https://addons.mozilla.org/firefox/addon/pwas-for-firefox/")

    def status(self) -> None:
        header(self, "Status navegador")
        self.ctx.runner.run(["pacman", "-Q", "firefox", "firefoxpwa"], check=False)
        self.ctx.runner.run(["flatpak", "info", "com.bitwarden.desktop"], check=False)
        self.ctx.runner.run(["firefox", "--version"], check=False)

    def undo(self) -> None:
        self.ctx.logger.write("Sugestao manual para Firefox/FirefoxPWA: sudo pacman -Rns firefox firefoxpwa")
        remove_flatpak("com.bitwarden.desktop", self.ctx.runner)


WEBAPPS = (
    ("ChatGPT", "chatgpt", "https://chatgpt.com", "https://chatgpt.com/manifest.json"),
    ("GSV Calendar", "gsv-calendar", "http://gsv-calendar.vercel.app", "https://gsv-calendar.vercel.app/manifest.json"),
)


class WebAppsStep(Step):
    id = "03"
    title = "WebApps"

    def apply(self) -> None:
        header(self, "WebApps com FirefoxPWA, WebApp Manager ou fallback")
        if self._all_webapps_present():
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} WebApps/atalhos ja encontrados. Pulando criacao.")
            return
        created = False
        if pacman_exists("firefoxpwa"):
            install_pacman("firefoxpwa", self.ctx.runner)
        if command_exists("firefoxpwa") or self.ctx.runner.dry_run:
            created = self._try_firefoxpwa()
        if not created:
            created = self._try_webapp_manager()
        if not created:
            self._create_desktop_fallbacks()
            self.ctx.logger.write(f"{Color.YELLOW}AVISO:{Color.RESET} Fallback criado. Estes atalhos nao sao PWAs reais.")

    def _try_firefoxpwa(self) -> bool:
        ok_all = True
        for name, slug, _url, manifest in WEBAPPS:
            if self._webapp_present(name, slug):
                self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} {name} ja encontrado")
                continue
            profile_id = None
            result = self.ctx.runner.run(
                ["firefoxpwa", "profile", "create", "--name", slug, "--description", name],
                check=False,
            )
            if result and result.stdout:
                match = re.search(r"Profile created:\s*([^\s]+)", result.stdout)
                profile_id = match.group(1) if match else None
            if self.ctx.runner.dry_run:
                profile_id = "PROFILE-ID"
            if not profile_id:
                ok_all = False
                continue
            install = self.ctx.runner.run(
                ["firefoxpwa", "site", "install", manifest, "--profile", profile_id, "--name", name],
                check=False,
            )
            ok_all = ok_all and (install is None or install.returncode == 0)
        if not ok_all:
            self.ctx.logger.write("FirefoxPWA nao conseguiu criar todos os apps. Verifique a extensao no Firefox.")
        return ok_all

    def _try_webapp_manager(self) -> bool:
        if not command_exists("webapp-manager"):
            install_system_or_aur("webapp-manager", "webapp-manager", self.ctx.runner)
        if command_exists("webapp-manager") or self.ctx.runner.dry_run:
            self.ctx.logger.write("Abrindo WebApp Manager para criacao manual assistida dos WebApps.")
            self.ctx.runner.run(["webapp-manager"], check=False)
            self.ctx.logger.write("Crie ChatGPT e GSV Calendar usando Firefox como navegador.")
            return True
        return False

    def _create_desktop_fallbacks(self) -> None:
        app_dir = self.ctx.user.home / ".local/share/applications"
        for name, slug, url, _manifest in WEBAPPS:
            if self._webapp_present(name, slug):
                self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} {name} ja encontrado")
                continue
            entry = DesktopEntry(
                name=name,
                comment=f"Fallback WebApp {name}",
                exec_line=f"firefox --new-window {url}",
                categories=("Network", "WebBrowser"),
            )
            install_desktop_entry(app_dir / f"{slug}.desktop", entry, self.ctx.runner)

    def _all_webapps_present(self) -> bool:
        return all(self._webapp_present(name, slug) for name, slug, _url, _manifest in WEBAPPS)

    def _webapp_present(self, name: str, slug: str) -> bool:
        app_dir = self.ctx.user.home / ".local/share/applications"
        candidates = [
            app_dir / f"{slug}.desktop",
            app_dir / f"{name.lower().replace(' ', '-')}.desktop",
        ]
        if any(path.exists() for path in candidates):
            return True
        if command_exists("firefoxpwa"):
            result = self.ctx.runner.run(["firefoxpwa", "site", "list"], check=False)
            return bool(result and name.lower() in result.stdout.lower())
        return False

    def status(self) -> None:
        header(self, "Status WebApps")
        self.ctx.runner.run(["firefoxpwa", "site", "list"], check=False)
        self.ctx.runner.run(["find", str(self.ctx.user.home / ".local/share/applications"), "-maxdepth", "1", "-iname", "*chatgpt*.desktop", "-o", "-iname", "*gsv*.desktop"], check=False)

    def undo(self) -> None:
        app_dir = self.ctx.user.home / ".local/share/applications"
        for _name, slug, _url, _manifest in WEBAPPS:
            target = app_dir / f"{slug}.desktop"
            if self.ctx.runner.dry_run:
                self.ctx.logger.write(f"{Color.YELLOW}[dry-run]{Color.RESET} removeria {target}")
            else:
                target.unlink(missing_ok=True)
        self.ctx.logger.write("Removidos apenas os fallbacks .desktop criados por esta etapa.")


class NvidiaSteamStep(Step):
    id = "04"
    title = "Validar NVIDIA / jogos / Steam"

    def apply(self) -> None:
        self.status()

    def status(self) -> None:
        header(self, self.title)
        self.ctx.logger.write(f"XDG_SESSION_TYPE={__import__('os').environ.get('XDG_SESSION_TYPE', '')}")
        self.ctx.runner.run(["glxinfo", "-B"], check=False)
        self.ctx.runner.run(["prime-run", "glxinfo", "-B"], check=False)
        self.ctx.runner.run(["nvidia-smi"], check=False)
        self.ctx.runner.run(["pacman", "-Q", "steam", "heroic-games-launcher"], check=False)


class GitStep(Step):
    id = "05"
    title = "Git / GitHub"

    def apply(self) -> None:
        header(self, self.title)
        install_pacman("git", self.ctx.runner)
        base = Path("/home/repositorios")
        target = base / "scripts-linux"
        if base.exists():
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} {base} ja existe")
        else:
            self.ctx.runner.run(["mkdir", "-p", str(base)], sudo=True)
        self.ctx.runner.run(["chown", f"{self.ctx.user.uid}:{self.ctx.user.gid}", str(base)], sudo=True)
        if self.ctx.runner.dry_run:
            self.ctx.logger.write(f"{Color.YELLOW}[dry-run]{Color.RESET} pediria a URL do repositorio e faria clone/pull")
            return
        repo_url = input("Repo URL scripts-linux (SSH/HTTPS): ").strip()
        if not repo_url:
            self.ctx.logger.write(f"{Color.YELLOW}AVISO:{Color.RESET} URL vazia, pulando clone.")
            return
        if (target / ".git").exists():
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} repositorio ja existe em {target}; atualizando")
            self.ctx.runner.run(["git", "-C", str(target), "pull", "--ff-only"], check=False)
        else:
            self.ctx.runner.run(["git", "clone", repo_url, str(target)])

    def status(self) -> None:
        header(self, self.title)
        self.ctx.runner.run(["git", "--version"], check=False)
        self.ctx.runner.run(["git", "-C", "/home/repositorios/scripts-linux", "status", "--short", "--branch"], check=False)


class RcloneStep(Step):
    id = "06"
    title = "Google Drive / rclone"
    remote = "Google Drive:"

    def apply(self) -> None:
        header(self, self.title)
        install_pacman("rclone", self.ctx.runner)
        mount_dir = self.ctx.user.home / "GoogleDrive"
        service_dir = self.ctx.user.home / ".config/systemd/user"
        service_file = service_dir / "rclone-google-drive.service"
        if not self.ctx.runner.dry_run:
            mount_dir.mkdir(parents=True, exist_ok=True)
            service_dir.mkdir(parents=True, exist_ok=True)
        remotes = self.ctx.runner.run(["rclone", "listremotes"], check=False)
        if remotes and self.remote not in remotes.stdout:
            self.ctx.logger.write("Remote 'Google Drive:' nao encontrado. Abrindo rclone config.")
            self.ctx.runner.run(["rclone", "config"], check=False)
        service_content = """[Unit]
Description=Rclone Google Drive mount
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/rclone mount 'Google Drive:' %h/GoogleDrive --vfs-cache-mode writes --dir-cache-time 72h --poll-interval 15s
ExecStop=/usr/bin/fusermount3 -u %h/GoogleDrive
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
"""
        service_was_current = service_file.exists() and service_file.read_text(encoding="utf-8", errors="ignore") == service_content
        write_text(
            service_file,
            service_content,
            self.ctx.runner,
        )
        if not service_was_current or self.ctx.runner.dry_run:
            self.ctx.runner.run(["systemctl", "--user", "daemon-reload"], check=False)
        if self._user_service_active("rclone-google-drive.service") and self._user_service_enabled("rclone-google-drive.service"):
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} rclone-google-drive.service ja esta habilitado e ativo")
        else:
            self.ctx.runner.run(["systemctl", "--user", "enable", "--now", "rclone-google-drive.service"], check=False)

    def _user_service_active(self, name: str) -> bool:
        result = self.ctx.runner.run(["systemctl", "--user", "is-active", "--quiet", name], check=False)
        return bool(result and result.returncode == 0)

    def _user_service_enabled(self, name: str) -> bool:
        result = self.ctx.runner.run(["systemctl", "--user", "is-enabled", "--quiet", name], check=False)
        return bool(result and result.returncode == 0)

    def status(self) -> None:
        header(self, self.title)
        self.ctx.runner.run(["rclone", "version"], check=False)
        self.ctx.runner.run(["rclone", "listremotes"], check=False)
        self.ctx.runner.run(["systemctl", "--user", "status", "rclone-google-drive.service", "--no-pager"], check=False)

    def undo(self) -> None:
        service_file = self.ctx.user.home / ".config/systemd/user/rclone-google-drive.service"
        self.ctx.runner.run(["systemctl", "--user", "disable", "--now", "rclone-google-drive.service"], check=False)
        if self.ctx.runner.dry_run:
            self.ctx.logger.write(f"{Color.YELLOW}[dry-run]{Color.RESET} removeria {service_file}")
        else:
            service_file.unlink(missing_ok=True)


class FstabStep(Step):
    id = "07"
    title = "Montagem de discos / fstab"
    labels = ("WINDOWS", "DADOS WINDOWS", "JOGOS LINUX")
    begin = "# BEGIN pos-formatacao-cachyos"
    end = "# END pos-formatacao-cachyos"

    def apply(self) -> None:
        header(self, self.title)
        if not self.ctx.runner.dry_run and not confirm_phrase("APLICAR-FSTAB"):
            return
        lines = self._build_lines()
        fstab = Path("/etc/fstab")
        current = fstab.read_text(encoding="utf-8")
        cleaned = self._without_block(current)
        content = cleaned.rstrip() + "\n\n" + "\n".join([self.begin, *lines, self.end]) + "\n"
        if current == content:
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} /etc/fstab ja contem o bloco esperado")
            self._ensure_mountpoints()
            self.ctx.runner.run(["mount", "-a"], sudo=True, check=False)
            return
        backup_existing(fstab, self.ctx.runner, sudo=True)
        self._ensure_mountpoints()
        write_text_sudo(fstab, content, self.ctx.runner)
        self.ctx.runner.run(["systemctl", "daemon-reload"], sudo=True)
        self.ctx.runner.run(["mount", "-a"], sudo=True, check=False)

    def _build_lines(self) -> list[str]:
        lines = []
        for label in self.labels:
            device = self._blkid_label(label)
            if not device:
                self.ctx.logger.write(f"{Color.YELLOW}AVISO:{Color.RESET} Label nao encontrado: {label}")
                continue
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
        return {"WINDOWS": "/mnt/windows", "DADOS WINDOWS": "/mnt/dados-windows", "JOGOS LINUX": "/mnt/jogos-linux"}[label]

    def _ensure_mountpoints(self) -> None:
        for label in self.labels:
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
        self.ctx.runner.run(["grep", "-n", "pos-formatacao-cachyos\\|/mnt/windows\\|/mnt/dados-windows\\|/mnt/jogos-linux", "/etc/fstab"], check=False)

    def undo(self) -> None:
        if not self.ctx.runner.dry_run and not confirm_phrase("REMOVER-FSTAB"):
            return
        fstab = Path("/etc/fstab")
        backup_existing(fstab, self.ctx.runner, sudo=True)
        write_text_sudo(fstab, self._without_block(fstab.read_text(encoding="utf-8")), self.ctx.runner)
        self.ctx.runner.run(["systemctl", "daemon-reload"], sudo=True)


class GesturesStep(Step):
    id = "08"
    title = "Gestos KDE"

    def apply(self) -> None:
        header(self, self.title)
        self.ctx.logger.write("Configuracao principal: use os gestos nativos do Plasma em Configuracoes > Touchpad > Gestos.")
        self.ctx.logger.write("Fallback opcional com libinput-gestures para gesto 3 dedos para cima.")
        if command_exists("libinput-gestures-setup"):
            self._write_libinput_config()
            self.ctx.runner.run(["libinput-gestures-setup", "autostart"], check=False)
            self.ctx.runner.run(["libinput-gestures-setup", "restart"], check=False)
        else:
            self.ctx.logger.write("libinput-gestures nao instalado. Nenhuma alteracao aplicada.")

    def _write_libinput_config(self) -> None:
        helper = self.ctx.user.home / ".local/bin/kde-gnome-like-overview"
        conf = self.ctx.user.home / ".config/libinput-gestures.conf"
        helper_content = """#!/usr/bin/env bash
qdbus6 org.kde.kglobalaccel /component/kwin org.kde.kglobalaccel.Component.invokeShortcut "Overview" >/dev/null 2>&1 && exit 0
qdbus6 org.kde.KWin /Effects org.kde.kwin.Effects.toggleEffect "overview" >/dev/null 2>&1 && exit 0
exit 1
"""
        conf_content = f"gesture swipe up 3 {helper}\n"
        write_text(
            helper,
            helper_content,
            self.ctx.runner,
            mode=0o755,
        )
        if conf.exists() and conf.read_text(encoding="utf-8", errors="ignore") == conf_content:
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} {conf} ja esta atualizado")
        else:
            backup_existing(conf, self.ctx.runner)
            write_text(conf, conf_content, self.ctx.runner)

    def status(self) -> None:
        header(self, self.title)
        self.ctx.runner.run(["printenv", "XDG_CURRENT_DESKTOP"], check=False)
        self.ctx.runner.run(["libinput-gestures-setup", "status"], check=False)
        self.ctx.runner.run(["cat", str(self.ctx.user.home / ".config/libinput-gestures.conf")], check=False)

    def undo(self) -> None:
        for path in (self.ctx.user.home / ".config/libinput-gestures.conf", self.ctx.user.home / ".local/bin/kde-gnome-like-overview"):
            if self.ctx.runner.dry_run:
                self.ctx.logger.write(f"{Color.YELLOW}[dry-run]{Color.RESET} removeria {path}")
            else:
                path.unlink(missing_ok=True)
        self.ctx.runner.run(["libinput-gestures-setup", "stop"], check=False)
        self.ctx.runner.run(["libinput-gestures-setup", "autostop"], check=False)


class AppsStep(Step):
    id = "09"
    title = "Apps jogos/comunicacao/dev"
    flatpaks = {
        "Discord": "com.discordapp.Discord",
        "TeamSpeak": "com.teamspeak.TeamSpeak",
        "ZapZap": "com.rtosta.zapzap",
        "ONLYOFFICE": "org.onlyoffice.desktopeditors",
        "Google Chrome": "com.google.Chrome",
        "Minecraft Bedrock Launcher": "io.mrarm.mcpelauncher",
        "Bitwarden": "com.bitwarden.desktop",
    }

    def apply(self) -> None:
        header(self, self.title)
        install_system_or_aur("steam", "steam", self.ctx.runner)
        install_system_or_aur("heroic-games-launcher", "heroic-games-launcher-bin", self.ctx.runner)
        self._install_hydra()
        for name, app_id in self.flatpaks.items():
            header(self, f"{name} - Flatpak")
            install_flatpak(app_id, self.ctx.runner)
        header(self, "Codex CLI")
        self.ctx.runner.run(["pacman", "-S", "--needed", "nodejs", "npm"], sudo=True)
        if npm_global_installed("@openai/codex"):
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} @openai/codex ja instalado globalmente")
        else:
            self.ctx.runner.run(["npm", "install", "-g", "@openai/codex"], sudo=True)

    def _install_hydra(self) -> None:
        header(self, "Hydra Launcher AppImage")
        install_pacman("curl", self.ctx.runner)
        install_pacman("fuse2", self.ctx.runner)
        appimage_dir = self.ctx.user.home / "AppImages"
        icon_source = self.ctx.root / "assets/hydra.png"
        icon_target = self.ctx.user.home / ".local/share/icons/hydra-launcher.png"
        if not self.ctx.runner.dry_run:
            appimage_dir.mkdir(parents=True, exist_ok=True)
        copy_asset(icon_source, icon_target, self.ctx.runner)
        out = appimage_dir / "HydraLauncher-latest.AppImage"
        desktop_file = self.ctx.user.home / ".local/share/applications/hydra-launcher.desktop"
        if out.exists() and desktop_file.exists() and icon_target.exists():
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} Hydra Launcher ja instalado")
            return
        url_cmd = "curl -fsSL https://api.github.com/repos/hydralauncher/hydra/releases/latest | grep -Eo 'https://[^\\\"]+\\.AppImage' | head -n1"
        result = self.ctx.runner.run(url_cmd, shell=True, check=False)
        url = result.stdout.strip() if result and result.stdout else "HYDRA_APPIMAGE_URL"
        if url == "HYDRA_APPIMAGE_URL" and not self.ctx.runner.dry_run:
            self.ctx.logger.write(f"{Color.YELLOW}AVISO:{Color.RESET} Nao encontrei AppImage do Hydra no release latest.")
            return
        self.ctx.runner.run(["curl", "-L", url, "-o", str(out)], check=False)
        self.ctx.runner.run(["chmod", "+x", str(out)], check=False)
        entry = DesktopEntry(
            name="Hydra Launcher",
            exec_line=f"{out} %U",
            icon=str(icon_target),
            categories=("Game",),
        )
        install_desktop_entry(desktop_file, entry, self.ctx.runner)

    def status(self) -> None:
        header(self, self.title)
        self.ctx.runner.run(["pacman", "-Q", "steam", "heroic-games-launcher", "nodejs", "npm"], check=False)
        self.ctx.runner.run(["flatpak", "list", "--app"], check=False)
        self.ctx.runner.run(["codex", "--version"], check=False)
        self.ctx.runner.run(["ls", "-l", str(self.ctx.user.home / "AppImages/HydraLauncher-latest.AppImage")], check=False)

    def undo(self) -> None:
        self.ctx.logger.write("Nao vou remover pacotes automaticamente. Removendo apenas Hydra AppImage/atalho/icone criados pela etapa.")
        for path in (
            self.ctx.user.home / "AppImages/HydraLauncher-latest.AppImage",
            self.ctx.user.home / ".local/share/applications/hydra-launcher.desktop",
            self.ctx.user.home / ".local/share/icons/hydra-launcher.png",
        ):
            if self.ctx.runner.dry_run:
                self.ctx.logger.write(f"{Color.YELLOW}[dry-run]{Color.RESET} removeria {path}")
            else:
                path.unlink(missing_ok=True)


class NumLockStep(Step):
    id = "10"
    title = "Fixar Num Lock"
    sddm_file = Path("/etc/sddm.conf.d/10-numlock.conf")

    def apply(self) -> None:
        header(self, self.title)
        kde_conf = self.ctx.user.home / ".config/kcminputrc"
        if command_exists("kwriteconfig6"):
            if self._kde_numlock_ready(kde_conf):
                self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} Num Lock do KDE ja esta configurado")
            else:
                backup_existing(kde_conf, self.ctx.runner)
                self.ctx.runner.run(["kwriteconfig6", "--file", "kcminputrc", "--group", "Keyboard", "--key", "NumLock", "0"], check=False)
        else:
            content = self._set_ini_value(kde_conf.read_text(encoding="utf-8") if kde_conf.exists() else "", "Keyboard", "NumLock", "0")
            if kde_conf.exists() and kde_conf.read_text(encoding="utf-8", errors="ignore") == content:
                self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} Num Lock do KDE ja esta configurado")
            else:
                backup_existing(kde_conf, self.ctx.runner)
                write_text(kde_conf, content, self.ctx.runner)
        self.ctx.runner.run(["mkdir", "-p", str(self.sddm_file.parent)], sudo=True)
        sddm_content = "[General]\nNumlock=on\n"
        if self.sddm_file.exists() and self.sddm_file.read_text(encoding="utf-8", errors="ignore") == sddm_content:
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} Num Lock do SDDM ja esta configurado")
        else:
            backup_existing(self.sddm_file, self.ctx.runner, sudo=True)
            write_text_sudo(self.sddm_file, sddm_content, self.ctx.runner)
        self._warn_sddm_conflicts()

    def _kde_numlock_ready(self, kde_conf: Path) -> bool:
        if not kde_conf.exists():
            return False
        text = kde_conf.read_text(encoding="utf-8", errors="ignore")
        return bool(re.search(r"(?ms)^\[Keyboard\].*^NumLock=0$", text))

    def _set_ini_value(self, text: str, section: str, key: str, value: str) -> str:
        lines = text.splitlines()
        out: list[str] = []
        in_section = False
        found_section = False
        wrote = False
        for line in lines:
            if line.strip().startswith("[") and line.strip().endswith("]"):
                if in_section and not wrote:
                    out.append(f"{key}={value}")
                    wrote = True
                in_section = line.strip() == f"[{section}]"
                found_section = found_section or in_section
            if in_section and line.startswith(f"{key}="):
                if not wrote:
                    out.append(f"{key}={value}")
                    wrote = True
                continue
            out.append(line)
        if not found_section:
            out.extend(["", f"[{section}]", f"{key}={value}"])
        elif in_section and not wrote:
            out.append(f"{key}={value}")
        return "\n".join(out).strip() + "\n"

    def _warn_sddm_conflicts(self) -> None:
        conf_dir = Path("/etc/sddm.conf.d")
        if not conf_dir.exists():
            return
        for path in conf_dir.glob("*.conf"):
            if path == self.sddm_file:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except PermissionError:
                continue
            if "Numlock" in text or "NumLock" in text:
                self.ctx.logger.write(f"{Color.YELLOW}AVISO:{Color.RESET} Possivel conflito SDDM: {path}")

    def status(self) -> None:
        header(self, self.title)
        self.ctx.runner.run(["grep", "-n", "NumLock", str(self.ctx.user.home / ".config/kcminputrc")], check=False)
        self.ctx.runner.run(["cat", str(self.sddm_file)], sudo=True, check=False)
        self.ctx.runner.run(["find", "/etc/sddm.conf.d", "-maxdepth", "1", "-type", "f", "-name", "*.conf"], check=False)

    def undo(self) -> None:
        if self.ctx.runner.dry_run:
            self.ctx.logger.write(f"{Color.YELLOW}[dry-run]{Color.RESET} removeria {self.sddm_file}")
        else:
            self.ctx.runner.run(["rm", "-f", str(self.sddm_file)], sudo=True)
        self.ctx.logger.write("A configuracao KDE do usuario foi preservada; use Configuracoes > Teclado para alterar se quiser.")


class AntigravityStep(Step):
    id = "11"
    title = "Google Antigravity IDE"
    url = "https://edgedl.me.gvt1.com/edgedl/release2/j0qc3/antigravity/stable/2.0.4-6381998290370560/linux-x64/Antigravity%20IDE.tar.gz"
    version = "2.0.4-6381998290370560"

    def apply(self) -> None:
        header(self, self.title)
        for pkg in ("curl", "tar", "desktop-file-utils", "findutils", "coreutils"):
            install_pacman(pkg, self.ctx.runner)
        cache = self.ctx.user.home / ".cache/antigravity-ide"
        tarball = cache / f"Antigravity-IDE-{self.version}.tar.gz"
        install_dir = self.ctx.user.home / "Antigravity IDE"
        existing_exe = self._find_executable(install_dir) if install_dir.exists() else None
        if existing_exe and self._desktop_ready(existing_exe) and self._wrapper_ready(existing_exe):
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} Antigravity IDE ja instalado e integrado")
            self._path_hint()
            return
        if not self.ctx.runner.dry_run:
            cache.mkdir(parents=True, exist_ok=True)
        backup_existing(install_dir, self.ctx.runner)
        if tarball.exists() and tarball.stat().st_size > 1024 * 1024:
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} pacote Antigravity ja esta em cache: {tarball}")
        else:
            self.ctx.runner.run(["curl", "-L", "--fail", "-o", str(tarball), self.url])
        tmp = self.ctx.user.home / ".cache/antigravity-ide/extract"
        if not self.ctx.runner.dry_run:
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir(parents=True, exist_ok=True)
        self.ctx.runner.run(["tar", "-xzf", str(tarball), "-C", str(tmp)])
        if self.ctx.runner.dry_run:
            exe = install_dir / "antigravity-ide"
            icon = install_dir / "resources/app/resources/linux/code.png"
        else:
            extracted = next((p for p in tmp.iterdir() if p.is_dir()), None)
            if not extracted:
                raise RuntimeError("nao encontrei diretorio extraido do Antigravity")
            shutil.rmtree(install_dir, ignore_errors=True)
            shutil.copytree(extracted, install_dir)
            exe = self._find_executable(install_dir)
            icon = self._find_icon(install_dir) or exe
        if not exe:
            raise RuntimeError("nao encontrei executavel antigravity-ide")
        self._write_desktop(exe, icon)
        self._write_terminal_wrapper(exe)
        ensure_owner(install_dir, self.ctx.user, self.ctx.runner, recursive=True)
        self._path_hint()

    def _find_executable(self, install_dir: Path) -> Path | None:
        for name in ("antigravity-ide", "antigravity", "code"):
            matches = list(install_dir.rglob(name))
            for match in matches:
                if match.is_file() and match.stat().st_mode & 0o111:
                    return match
        return None

    def _find_icon(self, install_dir: Path) -> Path | None:
        candidates = list(install_dir.rglob("code.png")) + list(install_dir.rglob("antigravity*.png"))
        return candidates[0] if candidates else None

    def _write_desktop(self, exe: Path, icon: Path) -> None:
        entry = DesktopEntry(
            name="Antigravity IDE",
            comment="Google Antigravity IDE",
            exec_line=f'"{exe}" %U',
            icon=str(icon),
            categories=("Development", "IDE"),
            mime_types=("text/plain", "inode/directory"),
            startup_wm_class="antigravity-ide",
        )
        install_desktop_entry(self.ctx.user.home / ".local/share/applications/antigravity-ide.desktop", entry, self.ctx.runner)

    def _write_terminal_wrapper(self, exe: Path) -> None:
        wrapper = self.ctx.user.home / ".local/bin/antigravity-ide"
        content = self._wrapper_content(exe)
        write_text(wrapper, content, self.ctx.runner, mode=0o755)

    def _wrapper_content(self, exe: Path) -> str:
        return f"""#!/usr/bin/env bash
nohup "{exe}" "$@" >/dev/null 2>&1 &
disown 2>/dev/null || true
"""

    def _desktop_ready(self, exe: Path) -> bool:
        desktop_file = self.ctx.user.home / ".local/share/applications/antigravity-ide.desktop"
        return desktop_file.exists() and str(exe) in desktop_file.read_text(encoding="utf-8", errors="ignore")

    def _wrapper_ready(self, exe: Path) -> bool:
        wrapper = self.ctx.user.home / ".local/bin/antigravity-ide"
        return wrapper.exists() and wrapper.read_text(encoding="utf-8", errors="ignore") == self._wrapper_content(exe)

    def _path_hint(self) -> None:
        local_bin = str(self.ctx.user.home / ".local/bin")
        import os

        if local_bin not in os.environ.get("PATH", "").split(":"):
            self.ctx.logger.write("Comando para fish, se ~/.local/bin nao estiver no PATH:")
            self.ctx.logger.write(f"fish_add_path {local_bin}")

    def status(self) -> None:
        header(self, self.title)
        self.ctx.runner.run(["ls", "-ld", str(self.ctx.user.home / "Antigravity IDE")], check=False)
        self.ctx.runner.run(["ls", "-l", str(self.ctx.user.home / ".local/share/applications/antigravity-ide.desktop")], check=False)
        self.ctx.runner.run(["ls", "-l", str(self.ctx.user.home / ".local/bin/antigravity-ide")], check=False)

    def undo(self) -> None:
        for path in (
            self.ctx.user.home / ".local/share/applications/antigravity-ide.desktop",
            self.ctx.user.home / ".local/bin/antigravity-ide",
            self.ctx.user.home / "Antigravity IDE",
        ):
            if self.ctx.runner.dry_run:
                self.ctx.logger.write(f"{Color.YELLOW}[dry-run]{Color.RESET} removeria {path}")
            elif path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)


ALL_STEPS: tuple[type[Step], ...] = (
    ShellyStep,
    UpdateSystemStep,
    LinuxToysStep,
    AppImageSupportStep,
    BrowserStep,
    WebAppsStep,
    NvidiaSteamStep,
    GitStep,
    RcloneStep,
    FstabStep,
    GesturesStep,
    AppsStep,
    NumLockStep,
    AntigravityStep,
)
