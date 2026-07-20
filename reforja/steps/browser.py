from __future__ import annotations

import re
from functools import partial

from ..core import (
    Color,
    badge,
    capture,
    command_exists,
)
from ..desktop import DesktopEntry, install_desktop_entry
from ..platform import (
    current_distro,
    install_system_or_aur,
    install_system_package,
    system_installed,
    system_package_exists,
    system_query_command,
)
from ..steps_base import Step, StepTask
from ._common import header

# (nome, slug, url, manifest, icone). O icone e a URL do icone REAL do site (o do
# manifest/favicon), nao um glifo generico de navegador: a GUI baixa em segundo
# plano e cacheia, caindo no icone de tema enquanto nao chega.
WEBAPPS = (
    (
        "ChatGPT",
        "chatgpt",
        "https://chatgpt.com",
        "https://chatgpt.com/manifest.json",
        "https://chatgpt.com/favicon.ico",
    ),
    (
        "GSV Calendar",
        "gsv-calendar",
        "http://gsv-calendar.vercel.app",
        "https://gsv-calendar.vercel.app/manifest.json",
        "https://gsv-calendar.vercel.app/gsv-logo.png?v=3",
    ),
    (
        "Gerenciador Financeiro",
        "gerenciador-financeiro",
        "https://gerenciador-financeiro-gold.vercel.app",
        "https://gerenciador-financeiro-gold.vercel.app/manifest.webmanifest",
        "https://gerenciador-financeiro-gold.vercel.app/icons/apple-touch-icon.png",
    ),
)

# Rotulo do item de navegador no menu (os demais itens vem de WEBAPPS).
_BROWSER_ITEM = "Firefox + FirefoxPWA (navegador e base para PWAs)"


class BrowserStep(Step):
    id = "03"
    title = "Navegador e WebApps"
    description = (
        "Instala o Firefox + FirefoxPWA e cria os WebApps do dia a dia (ChatGPT, GSV Calendar e "
        "Gerenciador Financeiro) "
        "num unico menu de selecao, com fallback para WebApp Manager ou atalho .desktop."
    )

    def tasks(self) -> list[StepTask]:
        items = [
            StepTask(
                key="navegador",
                label=_BROWSER_ITEM,
                description=(
                    "Instala o Firefox e o FirefoxPWA, que e o que permite transformar sites em "
                    "aplicativos com janela propria (os WebApps abaixo dependem dele)."
                ),
                short_description="Firefox + FirefoxPWA (base dos WebApps)",
                icon="firefox",
                category="navegador",
                detect=self._browser_ready,
                run=self._install_browser,
            )
        ]
        for name, slug, url, _manifest, icon in WEBAPPS:
            items.append(
                StepTask(
                    key=f"webapp-{slug}",
                    label=f"WebApp {name}",
                    description=(
                        f"Cria um app de janela propria para {url}, com atalho no menu. Usa o FirefoxPWA; "
                        "se ele falhar, tenta o WebApp Manager e, por ultimo, um atalho .desktop simples."
                    ),
                    short_description=f"App de janela propria para {name}",
                    icon=icon,
                    category="navegador",
                    detect=partial(self._webapp_detail, name, slug),
                    run=partial(self._create_one_webapp, name, slug),
                    remove=partial(self._remove_webapp, name, slug),
                    detail="nao criado",
                )
            )
        return items

    def apply(self) -> None:
        header(self, self.title, "Navegador principal + WebApps num unico passo")
        super().apply()

    # ------------------------------------------------------------------ navegador

    def _firefox_present(self) -> bool:
        # Robusto: conta como presente se o pacote existe OU o binario esta no PATH
        # (cobre variantes de pacote e ambientes onde a query de pacote nao resolve).
        return system_installed("firefox") or command_exists("firefox")

    def _browser_ready(self) -> bool:
        return self._firefox_present() and (system_installed("firefoxpwa") or command_exists("firefoxpwa"))

    def _webapp_detail(self, name: str, slug: str) -> str | bool:
        return "ja criado" if self._webapp_present(name, slug) else False

    def _create_one_webapp(self, name: str, slug: str) -> None:
        entry = next(item for item in WEBAPPS if item[1] == slug)
        self._create_webapps([entry])

    def _install_browser(self) -> None:
        install_system_package("firefox", self.ctx.runner)
        self._ensure_firefoxpwa()
        self.ctx.logger.write("Extensao FirefoxPWA: https://addons.mozilla.org/firefox/addon/pwas-for-firefox/")

    def _ensure_firefoxpwa(self) -> None:
        if system_package_exists("firefoxpwa"):
            install_system_package("firefoxpwa", self.ctx.runner)
        else:
            install_system_or_aur("firefoxpwa", "firefoxpwa", self.ctx.runner)

    # ------------------------------------------------------------------ webapps

    def _webapp_label(self, name: str, slug: str) -> str:
        return f"WebApp {name} {'(ja existe)' if self._webapp_present(name, slug) else '(nao criado)'}"

    def _create_webapps(self, chosen) -> str:
        if all(self._webapp_present(name, slug) for name, slug, _url, _manifest, *_ in chosen):
            self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} WebApps/atalhos ja encontrados. Pulando criacao.")
            return ""
        # Os WebApps dependem do FirefoxPWA; garante mesmo sem o item de navegador marcado.
        if not command_exists("firefoxpwa"):
            self._ensure_firefoxpwa()
        created = False
        if command_exists("firefoxpwa") or self.ctx.runner.dry_run:
            created = self._try_firefoxpwa(chosen)
        if not created:
            created = self._try_webapp_manager()
        if not created:
            self._create_desktop_fallbacks(chosen)
            self.ctx.logger.write(f"{badge('aviso', Color.WARNING)} Fallback criado. Estes atalhos nao sao PWAs reais.")
            self.mark_manual("Fallback .desktop criado; PWAs reais podem exigir ajuste manual.")
        return "webapps: " + ", ".join(name for name, _slug, _url, _manifest in chosen)

    def _try_firefoxpwa(self, webapps) -> bool:
        ok_all = True
        for name, slug, _url, manifest, *_ in webapps:
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
        for name, slug, url, _manifest, *_ in webapps:
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

    def _remove_webapp(self, name: str, slug: str) -> None:
        """Remove os atalhos .desktop do WebApp. O perfil FirefoxPWA (o site em si)
        pode ser removido a parte com `firefoxpwa profile remove` — deixamos como dica."""
        header(self, f"Remover WebApp {name}")
        app_dir = self.ctx.user.home / ".local/share/applications"
        for path in (
            app_dir / f"{slug}.desktop",
            app_dir / f"{name.lower().replace(' ', '-')}.desktop",
        ):
            self.ctx.runner.run(
                ["rm", "-f", str(path)],
                check=False,
                show_progress=False,
                action=f"Removendo {path}",
            )
        if command_exists("firefoxpwa"):
            self.add_hint(
                f"Para apagar o perfil do WebApp {name}: firefoxpwa profile list && firefoxpwa profile remove <id>"
            )

    def _firefoxpwa_profiles(self) -> str:
        """Saida do `firefoxpwa profile list`, lida DIRETO (fora do Runner).

        Deteccao tem de ler o estado real mesmo quando o Runner esta em dry-run
        (a sondagem do card usa dry-run); passar pelo Runner devolveria vazio e
        marcaria o WebApp como ausente. `capture` tambem nao polui o console.
        """
        if not command_exists("firefoxpwa"):
            return ""
        return capture(["firefoxpwa", "profile", "list"]).stdout

    def _webapp_present(self, name: str, slug: str) -> bool:
        app_dir = self.ctx.user.home / ".local/share/applications"
        candidates = [
            app_dir / f"{slug}.desktop",
            app_dir / f"{name.lower().replace(' ', '-')}.desktop",
        ]
        if any(path.exists() for path in candidates):
            return True
        listing = self._firefoxpwa_profiles().lower()
        if not listing:
            return False
        # Casa pelo nome do WebApp ou pelo dominio da URL (robusto a nomes iguais).
        if name.lower() in listing:
            return True
        url = next((u for n, s, u, *_m in WEBAPPS if s == slug), "")
        host = re.sub(r"^https?://", "", url).split("/")[0].lower()
        return bool(host and host in listing)

    # ------------------------------------------------------------------ status / undo

    def status(self) -> None:
        header(self, self.title, "Navegador, FirefoxPWA e WebApps")
        self.ctx.runner.run(system_query_command("firefox", "firefoxpwa"), check=False)
        self.ctx.runner.run(["firefox", "--version"], check=False)
        if command_exists("firefoxpwa"):
            self.ctx.runner.run(["firefoxpwa", "profile", "list"], check=False)

        browser_missing: list[str] = []
        if not self._firefox_present():
            browser_missing.append("firefox")
        if not (system_installed("firefoxpwa") or command_exists("firefoxpwa")):
            browser_missing.append("firefoxpwa")
        webapps_missing = [name for name, slug, _url, *_m in WEBAPPS if not self._webapp_present(name, slug)]

        for name, ok in (
            ("firefox", "firefox" not in browser_missing),
            ("firefoxpwa", "firefoxpwa" not in browser_missing),
        ):
            self.ctx.logger.write(f"{badge(name, Color.SUCCESS if ok else Color.WARNING)} {'OK' if ok else 'ausente'}")
        for name, slug, _url, *_m in WEBAPPS:
            ok = name not in webapps_missing
            self.ctx.logger.write(f"{badge(slug, Color.SUCCESS if ok else Color.WARNING)} {'OK' if ok else 'ausente'}")

        if not browser_missing and not webapps_missing:
            self.mark_applied("Firefox, FirefoxPWA e WebApps estao aplicados.")
        elif browser_missing:
            self.mark_pending(
                f"Navegador ainda esta incompleto: {', '.join(browser_missing)}.",
                missing=browser_missing + webapps_missing,
            )
        else:
            self.mark_attention(
                f"Navegador OK, mas faltam WebApps: {', '.join(webapps_missing)}.",
                attention=webapps_missing,
            )

    def undo(self) -> None:
        header(self, self.title, "Removendo atalhos de WebApp criados; navegador fica")
        app_dir = self.ctx.user.home / ".local/share/applications"
        for _name, slug, _url, _manifest, *_ in WEBAPPS:
            target = app_dir / f"{slug}.desktop"
            if self.ctx.runner.dry_run:
                self.ctx.logger.write(f"{badge('dry-run', Color.DRY_RUN)} removeria {target}")
            else:
                target.unlink(missing_ok=True)
        self.ctx.logger.write("Removidos apenas os fallbacks .desktop criados por esta etapa.")
        distro = current_distro()
        if distro.is_arch:
            cmd = "sudo pacman -Rns firefox firefoxpwa"
        elif distro.is_fedora:
            cmd = "sudo dnf remove firefox firefoxpwa"
        else:
            cmd = "sudo apt-get remove firefox firefoxpwa"
        self.ctx.logger.write(f"Sugestao manual para remover o navegador: {cmd}")
        self.mark_done("Atalhos de WebApp removidos; navegador preservado.")
