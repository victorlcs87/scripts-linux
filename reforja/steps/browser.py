from __future__ import annotations

import re

from ..core import (
    Color,
    badge,
    command_exists,
    select_many,
)
from ..desktop import DesktopEntry, install_desktop_entry
from ..installers import (
    flatpak_installed,
    install_flatpak,
    remove_flatpak,
)
from ..platform import (
    current_distro,
    install_system_or_aur,
    install_system_package,
    system_installed,
    system_package_exists,
    system_query_command,
)
from ..steps_base import Step
from ._common import header


class BrowserStep(Step):
    id = "03"
    title = "Navegador e extensoes"
    description = "Instala o Firefox, o FirefoxPWA (base para os WebApps) e o Bitwarden (Flatpak)."

    def apply(self) -> None:
        header(self, "Firefox sistema + FirefoxPWA + Bitwarden", "Preparando navegador principal e base para PWAs")
        install_system_package("firefox", self.ctx.runner)
        if system_package_exists("firefoxpwa"):
            install_system_package("firefoxpwa", self.ctx.runner)
        else:
            install_system_or_aur("firefoxpwa", "firefoxpwa", self.ctx.runner)
        install_flatpak("com.bitwarden.desktop", self.ctx.runner)
        self.ctx.logger.write("Extensao FirefoxPWA: https://addons.mozilla.org/firefox/addon/pwas-for-firefox/")
        self.mark_done("Navegador, FirefoxPWA e Bitwarden processados.")

    def status(self) -> None:
        header(self, "Status navegador")
        self.ctx.runner.run(system_query_command("firefox", "firefoxpwa"), check=False)
        self.ctx.runner.run(["flatpak", "info", "com.bitwarden.desktop"], check=False)
        self.ctx.runner.run(["firefox", "--version"], check=False)
        missing = []
        if not system_installed("firefox"):
            missing.append("firefox")
        if not (system_installed("firefoxpwa") or command_exists("firefoxpwa")):
            missing.append("firefoxpwa")
        if not flatpak_installed("com.bitwarden.desktop"):
            missing.append("bitwarden")
        if missing:
            self.mark_pending(f"Navegador ainda esta incompleto: {', '.join(missing)}.", missing=missing)
        else:
            self.mark_applied("Firefox, FirefoxPWA e Bitwarden estao aplicados.")

    def undo(self) -> None:
        distro = current_distro()
        if distro.is_arch:
            cmd = "sudo pacman -Rns firefox firefoxpwa"
        elif distro.is_fedora:
            cmd = "sudo dnf remove firefox firefoxpwa"
        else:
            cmd = "sudo apt-get remove firefox firefoxpwa"
        self.ctx.logger.write(f"Sugestao manual para Firefox/FirefoxPWA: {cmd}")
        remove_flatpak("com.bitwarden.desktop", self.ctx.runner)


WEBAPPS = (
    ("ChatGPT", "chatgpt", "https://chatgpt.com", "https://chatgpt.com/manifest.json"),
    ("GSV Calendar", "gsv-calendar", "http://gsv-calendar.vercel.app", "https://gsv-calendar.vercel.app/manifest.json"),
)


class WebAppsStep(Step):
    id = "04"
    title = "WebApps"
    description = (
        "Cria atalhos de WebApp (ChatGPT e GSV Calendar) via FirefoxPWA, "
        "com fallback para WebApp Manager ou atalho .desktop."
    )

    def apply(self) -> None:
        header(self, "WebApps com FirefoxPWA, WebApp Manager ou fallback", "Criando atalhos e PWAs para uso diario")
        labels = [self._choice_label(name, slug) for name, slug, _url, _manifest in WEBAPPS]
        indices = select_many(
            "Quais WebApps criar",
            labels,
            self.ctx.logger,
            detail="Marque um ou mais. Nada marcado = nada a fazer.",
        )
        if not indices:
            self.mark_skipped("Nenhum WebApp selecionado.")
            return
        chosen = [WEBAPPS[i] for i in indices]
        if all(self._webapp_present(name, slug) for name, slug, _url, _manifest in chosen):
            self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} WebApps/atalhos ja encontrados. Pulando criacao.")
            self.mark_skipped("WebApps/atalhos ja existentes.")
            return
        created = False
        if system_package_exists("firefoxpwa"):
            install_system_package("firefoxpwa", self.ctx.runner)
        if command_exists("firefoxpwa") or self.ctx.runner.dry_run:
            created = self._try_firefoxpwa(chosen)
        if not created:
            created = self._try_webapp_manager()
        if not created:
            self._create_desktop_fallbacks(chosen)
            self.ctx.logger.write(f"{badge('aviso', Color.WARNING)} Fallback criado. Estes atalhos nao sao PWAs reais.")
            self.mark_manual("Fallback .desktop criado; PWAs reais podem exigir ajuste manual.")
            return
        self.mark_done("WebApps processados.")
        self.result.applied_items = [name for name, _slug, _url, _manifest in chosen]

    def _choice_label(self, name: str, slug: str) -> str:
        return f"{name} (ja existe)" if self._webapp_present(name, slug) else f"{name} (nao criado)"

    def _try_firefoxpwa(self, webapps) -> bool:
        ok_all = True
        for name, slug, _url, manifest in webapps:
            if self._webapp_present(name, slug):
                self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} {name} ja encontrado")
                continue
            profile_id = None
            result = self.ctx.runner.run(
                ["firefoxpwa", "profile", "create", "--name", slug, "--description", name],
                check=False,
                action=f"Criando perfil FirefoxPWA para {name}",
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
                action=f"Instalando WebApp {name} com FirefoxPWA",
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
            self.ctx.runner.run(
                ["webapp-manager"],
                check=False,
                action="Abrindo WebApp Manager para configuracao assistida",
                interactive=True,
                interactive_tty=True,
                manual_message="Comando interativo: crie os WebApps na interface. Isso nao e travamento.",
            )
            self.ctx.logger.write("Crie ChatGPT e GSV Calendar usando Firefox como navegador.")
            return True
        return False

    def _create_desktop_fallbacks(self, webapps) -> None:
        app_dir = self.ctx.user.home / ".local/share/applications"
        for name, slug, url, _manifest in webapps:
            if self._webapp_present(name, slug):
                self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} {name} ja encontrado")
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
            result = self.ctx.runner.run(["firefoxpwa", "profile", "list"], check=False)
            return bool(result and name.lower() in result.stdout.lower())
        return False

    def status(self) -> None:
        header(self, "Status WebApps")
        if command_exists("firefoxpwa"):
            self.ctx.runner.run(["firefoxpwa", "profile", "list"], check=False)
        else:
            self.ctx.logger.write("firefoxpwa nao esta instalado; status via CLI indisponivel.")
        self.ctx.runner.run(
            [
                "find",
                str(self.ctx.user.home / ".local/share/applications"),
                "-maxdepth",
                "1",
                "-iname",
                "*chatgpt*.desktop",
                "-o",
                "-iname",
                "*gsv*.desktop",
            ],
            check=False,
        )
        if self._all_webapps_present():
            self.mark_applied("ChatGPT e GSV Calendar estao presentes como WebApp ou atalho.")
        else:
            self.mark_pending("Ainda faltam WebApps/atalhos esperados.", missing=["ChatGPT", "GSV Calendar"])

    def undo(self) -> None:
        app_dir = self.ctx.user.home / ".local/share/applications"
        for _name, slug, _url, _manifest in WEBAPPS:
            target = app_dir / f"{slug}.desktop"
            if self.ctx.runner.dry_run:
                self.ctx.logger.write(f"{badge('dry-run', Color.DRY_RUN)} removeria {target}")
            else:
                target.unlink(missing_ok=True)
        self.ctx.logger.write("Removidos apenas os fallbacks .desktop criados por esta etapa.")
