from __future__ import annotations

import grp
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .core import (
    Color,
    PromptInterruptedError,
    announce,
    badge,
    backup_existing,
    command_exists,
    confirm_phrase,
    divider,
    ensure_owner,
    load_env_file,
    paint,
    print_lines,
    prompt_user,
    write_text,
    write_text_sudo,
)
from . import hardware
from .desktop import DesktopEntry, install_desktop_entry
from .installers import (
    aur_helper,
    copy_asset,
    ensure_flatpak,
    flatpak_installed,
    install_first_available,
    install_flatpak,
    install_pacman,
    install_system_package,
    install_system_or_aur,
    npm_global_installed,
    pacman_exists,
    remove_flatpak,
    system_installed,
    system_package_exists,
)
from .platform import current_distro, pending_updates_command, system_query_command, update_system
from .steps_base import Step


def header(step: Step, title: str, subtitle: str | None = None) -> None:
    step.ctx.logger.write("")
    step.ctx.logger.write(divider(char="#", tone=Color.TITLE))
    step.ctx.logger.write(f"{badge(step.id, Color.TITLE)} {paint(title, Color.TITLE)}")
    if subtitle:
        step.ctx.logger.write(paint(subtitle, Color.ACCENT))
    step.ctx.logger.write(divider())


@dataclass
class ProbeResult:
    label: str
    status: str
    summary: str
    details: str = ""


class ShellyStep(Step):
    id = "00"
    title = "Preparar ecossistema"

    def apply(self) -> None:
        distro = current_distro()
        header(self, self.title, "Preparando base de pacotes, Flatpak e suporte AppImage")
        ready_before = self._basic_support_ready()
        if distro.is_arch and not command_exists("shelly"):
            self.ctx.logger.write(f"{badge('aviso', Color.WARNING)} Shelly nao encontrado. Vou preparar o suporte pelo sistema mesmo assim.")
        elif distro.is_arch:
            self.ctx.logger.write(f"{badge('info', Color.INFO)} Shelly CLI detectado com suporte a flatpak, appimage e aur.")
        else:
            self.ctx.logger.write(f"{badge('info', Color.INFO)} Debian/Ubuntu detectado. Vou usar apt e opcoes do sistema, sem Shelly/AUR.")
        ensure_flatpak(self.ctx.runner)
        self._ensure_appimage_fuse()
        if distro.is_arch:
            self._ensure_aur_helper()
        if self._basic_support_ready():
            if ready_before:
                self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} Flatpak, flathub e suporte AppImage ja estavam prontos.")
                self.mark_skipped("Flatpak, flathub e suporte AppImage ja estavam prontos.")
                self.mark_applied("Flatpak, flathub e suporte AppImage estao prontos.")
            else:
                self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} Ecossistema preparado com sucesso.")
                self.mark_done("Ecossistema base preparado com sucesso.")
                self.mark_applied("Ecossistema base preparado com sucesso.")
            return
        if not distro.is_arch:
            self.ctx.logger.write(f"{badge('aviso', Color.WARNING)} Ainda faltam requisitos do ecossistema.")
            self.mark_pending("Faltam requisitos do ecossistema no Debian/Ubuntu.", missing=self._missing_basic_support())
            return
        self.ctx.logger.write(f"{badge('aviso', Color.WARNING)} Ainda faltam requisitos. Vou abrir o fallback assistido do Shelly.")
        if self.ctx.runner.dry_run:
            self.ctx.logger.write(f"{badge('dry-run', Color.DRY_RUN)} abriria Shelly ou Shelly UI para concluir ajustes manuais")
            self.mark_manual("Dry-run indica abertura manual do Shelly para concluir requisitos.")
            self.mark_attention("Ainda faltam requisitos e seria necessario concluir ajustes manuais no Shelly.")
            return
        if command_exists("shelly-ui"):
            self.ctx.runner.run(
                ["shelly-ui"],
                check=False,
                action="Abrindo Shelly UI",
                interactive=True,
                interactive_tty=True,
                manual_message="Comando interativo: o Shelly UI pode ficar aguardando sua acao. Isso nao e travamento.",
            )
        elif command_exists("shelly"):
            self.ctx.runner.run(
                ["shelly"],
                check=False,
                action="Abrindo Shelly",
                interactive=True,
                interactive_tty=True,
                manual_message="Comando interativo: o Shelly pode ficar aguardando sua acao. Isso nao e travamento.",
            )
        elif command_exists("cachyos-hello"):
            self.ctx.runner.run(
                ["cachyos-hello", "launch", "package-installer"],
                check=False,
                action="Abrindo CachyOS Hello para ajuste manual",
                interactive=True,
                interactive_tty=True,
                manual_message="Comando interativo: a interface pode ficar aguardando voce.",
            )
        else:
            self.ctx.logger.write(f"{badge('aviso', Color.WARNING)} Shelly/CachyOS Hello nao encontrado.")
            self.mark_manual("Nao encontrei Shelly/CachyOS Hello; ajuste manual necessario.")
            self.mark_pending("Faltam requisitos do ecossistema e nao ha fallback grafico disponivel.")
            return
        prompt_user(
            "Pressione ENTER depois de revisar no Shelly",
            self.ctx.logger,
            detail="O sisteminha esta pausado aguardando voce confirmar que terminou a revisao.",
            prompt_label="ENTER",
        )
        self.mark_manual("Etapa dependeu de revisao manual no Shelly.")
        self.mark_attention("Ecossistema dependeu de revisao manual no Shelly.")

    def status(self) -> None:
        header(self, "Status do ecossistema", "Resumo do que ja esta pronto antes das proximas etapas")
        distro = current_distro()
        flatpak_ready = command_exists("flatpak")
        flathub_ready = self._flathub_ready() if flatpak_ready else False
        fuse2_ready = self._appimage_fuse_ready()
        helper = aur_helper()
        lines = [
            f"{badge('distro', Color.INFO)} {distro.id or distro.family} ({distro.family})",
            f"{badge('flatpak', Color.SUCCESS if flatpak_ready else Color.WARNING)} {'OK' if flatpak_ready else 'ausente'}",
            f"{badge('flathub', Color.SUCCESS if flathub_ready else Color.WARNING)} {'OK' if flathub_ready else 'ausente'}",
            f"{badge('appimage-fuse', Color.SUCCESS if fuse2_ready else Color.WARNING)} {'OK' if fuse2_ready else 'ausente'}",
        ]
        if distro.is_arch:
            lines.extend([
                f"{badge('shelly', Color.INFO)} {'OK' if command_exists('shelly') else 'ausente'}",
                f"{badge('shelly-ui', Color.INFO)} {'OK' if command_exists('shelly-ui') else 'ausente'}",
                f"{badge('cachyos-hello', Color.INFO)} {'OK' if command_exists('cachyos-hello') else 'ausente'}",
                f"{badge('aur', Color.SUCCESS if helper else Color.WARNING)} {helper or 'ausente'}",
            ])
        print_lines(self.ctx.logger, lines)
        if distro.is_arch and command_exists("shelly") and flatpak_ready:
            self.ctx.runner.run(["shelly", "flatpak", "list-remotes"], check=False, action="Verificando remotes do Shelly", show_progress=False)
        if self._basic_support_ready():
            self.mark_applied("Flatpak, flathub e suporte AppImage estao aplicados.")
        else:
            missing = self._missing_basic_support()
            self.mark_pending(f"Faltam componentes do ecossistema: {', '.join(missing)}.", missing=missing)

    def _basic_support_ready(self) -> bool:
        base_ready = command_exists("flatpak") and self._flathub_ready() and self._appimage_fuse_ready()
        if current_distro().is_arch:
            return base_ready and aur_helper() is not None
        return base_ready

    def _missing_basic_support(self) -> list[str]:
        missing = []
        if not command_exists("flatpak"):
            missing.append("flatpak")
        if command_exists("flatpak") and not self._flathub_ready():
            missing.append("flathub")
        if not self._appimage_fuse_ready():
            missing.append("suporte AppImage/FUSE")
        if current_distro().is_arch and not aur_helper():
            missing.append("helper AUR")
        return missing

    def _flathub_ready(self) -> bool:
        result = self.ctx.runner.run(["flatpak", "remote-list", "--columns=name"], check=False, action="Verificando remotes Flatpak", show_progress=False, quiet_success=True)
        if result and result.stdout:
            return any(line.strip() == "flathub" for line in result.stdout.splitlines())
        return self.ctx.runner.dry_run and command_exists("flatpak")

    def _ensure_appimage_fuse(self) -> None:
        if current_distro().is_arch:
            install_first_available(("fuse2",), self.ctx.runner)
        else:
            install_first_available(("libfuse2t64", "libfuse2", "fuse"), self.ctx.runner)

    def _appimage_fuse_ready(self) -> bool:
        if current_distro().is_arch:
            return system_installed("fuse2")
        return any(system_installed(pkg) for pkg in ("libfuse2t64", "libfuse2", "fuse"))

    def _ensure_aur_helper(self) -> None:
        if aur_helper():
            self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} AUR helper detectado: {aur_helper()}")
            return
        for candidate in ("paru", "yay"):
            if pacman_exists(candidate):
                install_pacman(candidate, self.ctx.runner)
                if aur_helper():
                    self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} AUR helper preparado: {aur_helper()}")
                    return
        self.ctx.logger.write(f"{badge('aviso', Color.WARNING)} Nao consegui instalar automaticamente um helper AUR.")

    def undo(self) -> None:
        self.ctx.logger.write("Nao ha undo seguro para Flatpak/flathub/fuse2/AUR helper. Se quiser, remova manualmente os componentes preparados.")


class UpdateSystemStep(Step):
    id = "01"
    title = "Atualizar sistema"

    def apply(self) -> None:
        header(self, self.title, "Atualizando a base do sistema e pacotes instalados")
        update_system(self.ctx.runner)
        self.ctx.logger.write(f"{Color.YELLOW}AVISO:{Color.RESET} Reinicie apos atualizacao grande/kernel.")
        self.mark_done("Atualizacao do sistema executada.")

    def status(self) -> None:
        header(self, "Status sistema")
        self.ctx.runner.run(["uname", "-r"], check=False)
        updates_cmd = pending_updates_command()
        updates = self.ctx.runner.run(
            updates_cmd,
            shell=True,
            check=False,
            action="Verificando atualizacoes pendentes",
            quiet_success=True,
        )
        if updates and updates.stdout.strip():
            self.ctx.logger.write(updates.stdout.rstrip())
            self.mark_attention("Existem atualizacoes pendentes no sistema.")
        else:
            self.ctx.logger.write("Nenhuma atualizacao pendente detectada.")
            self.mark_applied("Sistema sem atualizacoes pendentes.")

    def undo(self) -> None:
        self.ctx.logger.write("Nao ha undo seguro para uma atualizacao completa. Use snapshots se estiverem configurados.")


class LinuxToysStep(Step):
    id = "02"
    title = "Instalar Linux Toys"

    def apply(self) -> None:
        header(self, self.title, "Instalando utilitarios do Linux Toys")
        if command_exists("linuxtoys") or system_installed("linuxtoys"):
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} Linux Toys ja parece instalado")
            self.mark_skipped("Linux Toys ja parece instalado.")
            return
        build_dir = Path("/tmp/linuxtoys")
        if build_dir.exists():
            self.ctx.logger.write(f"{Color.YELLOW}AVISO:{Color.RESET} limpando build anterior em {build_dir} para evitar falha de makepkg")
            self.ctx.runner.run(["rm", "-rf", str(build_dir)], check=False, action="Limpando build temporario anterior do Linux Toys", show_progress=False)
        self.ctx.runner.run("curl -fsSL https://linux.toys/install.sh | bash", shell=True, action="Baixando e executando instalador do Linux Toys")
        self.mark_done("Linux Toys instalado.")

    def status(self) -> None:
        header(self, self.title, "Verificando presenca do Linux Toys")
        self.ctx.runner.run(system_query_command("linuxtoys"), check=False)
        self.ctx.runner.run(["linuxtoys", "--help"], check=False)
        if command_exists("linuxtoys") or system_installed("linuxtoys"):
            self.mark_applied("Linux Toys esta instalado.")
        else:
            self.mark_pending("Linux Toys ainda nao esta instalado.", missing=["linuxtoys"])


class BrowserStep(Step):
    id = "03"
    title = "Navegador e extensoes"

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
            self.ctx.logger.write("Sugestao manual para Firefox/FirefoxPWA: sudo pacman -Rns firefox firefoxpwa")
        else:
            self.ctx.logger.write("Sugestao manual para Firefox/FirefoxPWA: sudo apt-get remove firefox firefoxpwa")
        remove_flatpak("com.bitwarden.desktop", self.ctx.runner)


WEBAPPS = (
    ("ChatGPT", "chatgpt", "https://chatgpt.com", "https://chatgpt.com/manifest.json"),
    ("GSV Calendar", "gsv-calendar", "http://gsv-calendar.vercel.app", "https://gsv-calendar.vercel.app/manifest.json"),
)


class WebAppsStep(Step):
    id = "04"
    title = "WebApps"

    def apply(self) -> None:
        header(self, "WebApps com FirefoxPWA, WebApp Manager ou fallback", "Criando atalhos e PWAs para uso diario")
        if self._all_webapps_present():
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} WebApps/atalhos ja encontrados. Pulando criacao.")
            self.mark_skipped("WebApps/atalhos ja existentes.")
            return
        created = False
        if system_package_exists("firefoxpwa"):
            install_system_package("firefoxpwa", self.ctx.runner)
        if command_exists("firefoxpwa") or self.ctx.runner.dry_run:
            created = self._try_firefoxpwa()
        if not created:
            created = self._try_webapp_manager()
        if not created:
            self._create_desktop_fallbacks()
            self.ctx.logger.write(f"{Color.YELLOW}AVISO:{Color.RESET} Fallback criado. Estes atalhos nao sao PWAs reais.")
            self.mark_manual("Fallback .desktop criado; PWAs reais podem exigir ajuste manual.")
            return
        self.mark_done("WebApps processados.")

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
            result = self.ctx.runner.run(["firefoxpwa", "profile", "list"], check=False)
            return bool(result and name.lower() in result.stdout.lower())
        return False

    def status(self) -> None:
        header(self, "Status WebApps")
        if command_exists("firefoxpwa"):
            self.ctx.runner.run(["firefoxpwa", "profile", "list"], check=False)
        else:
            self.ctx.logger.write("firefoxpwa nao esta instalado; status via CLI indisponivel.")
        self.ctx.runner.run(["find", str(self.ctx.user.home / ".local/share/applications"), "-maxdepth", "1", "-iname", "*chatgpt*.desktop", "-o", "-iname", "*gsv*.desktop"], check=False)
        if self._all_webapps_present():
            self.mark_applied("ChatGPT e GSV Calendar estao presentes como WebApp ou atalho.")
        else:
            self.mark_pending("Ainda faltam WebApps/atalhos esperados.", missing=["ChatGPT", "GSV Calendar"])

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
    id = "05"
    title = "Validar NVIDIA / jogos / Steam"

    def apply(self) -> None:
        results = self._collect_gpu_results()
        self._render_gpu_summary(results)
        ok_count = sum(1 for item in results if item.status == "ok")
        warn_count = sum(1 for item in results if item.status == "warn")
        problem_count = sum(1 for item in results if item.status == "problem")
        if problem_count == 0:
            self.mark_done(f"Validacao concluida: {ok_count} OK e {warn_count} alerta(s).")
            if warn_count == 0:
                self.mark_applied("Sessao grafica, GPUs e launchers estao conforme esperado.")
            else:
                self.mark_attention(f"Validacao concluida com {warn_count} alerta(s), sem falhas criticas.")
        else:
            self.mark_done(f"Validacao concluida com problemas: {problem_count} item(ns) exigem revisao.")
            self.mark_attention(f"Ha {problem_count} item(ns) que exigem revisao na validacao de GPU/jogos.")

    def status(self) -> None:
        results = self._collect_gpu_results()
        self._render_gpu_summary(results)
        ok_count = sum(1 for item in results if item.status == "ok")
        warn_count = sum(1 for item in results if item.status == "warn")
        problem_count = sum(1 for item in results if item.status == "problem")
        if problem_count == 0:
            if warn_count == 0:
                self.mark_applied("Sessao grafica, GPUs e launchers estao conforme esperado.")
            else:
                self.mark_attention(f"Validacao concluida com {warn_count} alerta(s), sem falhas criticas.")
        else:
            self.mark_attention(f"Ha {problem_count} item(ns) que exigem revisao na validacao de GPU/jogos.")

    def _collect_gpu_results(self) -> list[ProbeResult]:
        return [
            self._probe_session_type(),
            self._probe_integrated_gl(),
            self._probe_prime_gl(),
            self._probe_nvidia_smi(),
            *self._probe_launchers(),
        ]

    def _probe_session_type(self) -> ProbeResult:
        session_type = os.environ.get("XDG_SESSION_TYPE", "").strip().lower()
        if session_type in {"wayland", "x11"}:
            return ProbeResult("Sessao grafica", "ok", f"{session_type} detectado.")
        if session_type:
            return ProbeResult("Sessao grafica", "warn", f"valor incomum detectado: {session_type}.")
        return ProbeResult("Sessao grafica", "warn", "XDG_SESSION_TYPE nao foi detectado.")

    def _probe_integrated_gl(self) -> ProbeResult:
        if not command_exists("glxinfo"):
            return ProbeResult("OpenGL (GPU primaria)", "problem", "glxinfo nao esta instalado.", "Instale mesa-utils para validar o OpenGL.")
        result = self._run_probe(["glxinfo", "-B"], "Consultando capacidades OpenGL")
        output = self._combined_output(result).lower()
        if result.returncode == 0 and "direct rendering: yes" in output and ("opengl renderer string:" in output or "vendor:" in output):
            summary = self._extract_renderer_summary(result.stdout) or "OpenGL respondeu corretamente."
            return ProbeResult("OpenGL (GPU primaria)", "ok", summary)
        return ProbeResult(
            "OpenGL (GPU primaria)",
            "problem",
            "OpenGL basico nao respondeu como esperado.",
            self._truncate_probe_output(result),
        )

    def _gpu_count(self) -> int:
        if not command_exists("lspci"):
            return 0
        result = self._run_probe(["lspci"], "Listando dispositivos PCI")
        if result.returncode != 0:
            return 0
        return len(hardware.list_gpus(result.stdout))

    def _probe_prime_gl(self) -> ProbeResult:
        if not command_exists("prime-run"):
            if self._gpu_count() == 1:
                return ProbeResult("GPU NVIDIA dedicada", "ok", "nao aplicavel: maquina com GPU unica (prime-run e para notebooks hibridos).")
            return ProbeResult("GPU NVIDIA dedicada", "warn", "prime-run nao esta disponivel no sistema.")
        if not command_exists("glxinfo"):
            return ProbeResult("GPU NVIDIA dedicada", "problem", "glxinfo nao esta instalado para testar o prime-run.")
        result = self._run_probe(["prime-run", "glxinfo", "-B"], "Consultando OpenGL via GPU dedicada")
        output = self._combined_output(result).lower()
        if result.returncode == 0 and "nvidia" in output and "opengl renderer string:" in output:
            summary = self._extract_renderer_summary(result.stdout) or "prime-run respondeu com a GPU NVIDIA."
            return ProbeResult("GPU NVIDIA dedicada", "ok", summary)
        return ProbeResult(
            "GPU NVIDIA dedicada",
            "problem",
            "prime-run nao confirmou uso da GPU NVIDIA.",
            self._truncate_probe_output(result),
        )

    def _probe_nvidia_smi(self) -> ProbeResult:
        if not command_exists("nvidia-smi"):
            return ProbeResult("Driver NVIDIA", "problem", "nvidia-smi nao esta disponivel.")
        result = self._run_probe(["nvidia-smi"], "Verificando driver NVIDIA")
        output = self._combined_output(result).lower()
        if result.returncode == 0 and "nvidia-smi" in output and "gpu" in output:
            gpu_name = hardware.nvidia_gpu_name(result.stdout)
            summary = f"nvidia-smi respondeu corretamente ({gpu_name})." if gpu_name else "nvidia-smi respondeu corretamente."
            return ProbeResult("Driver NVIDIA", "ok", summary)
        return ProbeResult(
            "Driver NVIDIA",
            "problem",
            "nvidia-smi nao retornou um estado valido da GPU.",
            self._truncate_probe_output(result),
        )

    def _probe_launchers(self) -> list[ProbeResult]:
        steam_system = any(system_installed(pkg) for pkg in ("steam", "steam-installer", "steam-launcher"))
        heroic_system = any(system_installed(pkg) for pkg in ("heroic-games-launcher", "heroic-games-launcher-bin"))
        heroic_flatpak = flatpak_installed("com.heroicgameslauncher.hgl")
        probes: list[ProbeResult] = []
        probes.append(
            ProbeResult(
                "Steam",
                "ok" if steam_system else "warn",
                "instalado." if steam_system else "ausente.",
            )
        )
        heroic_installed = heroic_system or heroic_flatpak
        probes.append(
            ProbeResult(
                "Heroic",
                "ok" if heroic_installed else "warn",
                "instalado." if heroic_installed else "ausente.",
            )
        )
        return probes

    def _run_probe(self, cmd: list[str], action: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                cmd,
                cwd=str(self.ctx.root),
                env=os.environ.copy(),
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            return subprocess.CompletedProcess(args=cmd, returncode=127, stdout="", stderr=str(exc))

    def _render_gpu_summary(self, results: list[ProbeResult]) -> None:
        header(self, self.title, "Diagnostico amigavel de sessao grafica, GPUs e launchers")
        tone_map = {
            "ok": Color.SUCCESS,
            "warn": Color.WARNING,
            "problem": Color.ERROR,
        }
        label_map = {
            "ok": "ok",
            "warn": "atencao",
            "problem": "falha",
        }
        for item in results:
            self.ctx.logger.write(f"{badge(label_map[item.status], tone_map[item.status])} {item.label}: {item.summary}")
            if item.details:
                self.ctx.logger.write(paint(f"Detalhes: {item.details}", Color.MUTED))
        problem_count = sum(1 for item in results if item.status == "problem")
        warn_count = sum(1 for item in results if item.status == "warn")
        if problem_count == 0 and warn_count == 0:
            announce(self.ctx.logger, "done", "Tudo certo com sessao grafica, GPUs e launchers avaliados.")
        elif problem_count == 0:
            announce(self.ctx.logger, "warning", f"Validacao parcialmente pronta: {warn_count} alerta(s), sem falhas criticas.")
        else:
            announce(self.ctx.logger, "failed", f"Problema(s) detectado(s): {problem_count} item(ns) exigem revisao.")

    def _extract_renderer_summary(self, output: str) -> str:
        for raw_line in output.splitlines():
            line = raw_line.strip()
            if line.lower().startswith("opengl renderer string:"):
                value = line.split(":", 1)[1].strip()
                return f"renderer detectado: {value}."
        return ""

    def _combined_output(self, result: subprocess.CompletedProcess[str]) -> str:
        return "\n".join(part for part in [result.stdout or "", getattr(result, "stderr", "") or ""] if part).strip()

    def _truncate_probe_output(self, result: subprocess.CompletedProcess[str], limit: int = 220) -> str:
        combined = self._combined_output(result)
        if not combined:
            return f"retorno {result.returncode} sem detalhes adicionais."
        normalized = " ".join(combined.split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: limit - 3] + "..."


class GitStep(Step):
    id = "06"
    title = "Git / GitHub"

    def apply(self) -> None:
        header(self, self.title, "Preparando clone ou atualizacao do repositorio base")
        install_system_package("git", self.ctx.runner)
        base = self.ctx.user.home / "repositorios"
        target = base / "scripts-linux"
        if base.exists():
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} {base} ja existe")
        else:
            self.ctx.runner.run(["mkdir", "-p", str(base)], action=f"Criando diretorio {base}", show_progress=False)
        if self.ctx.runner.dry_run:
            self.ctx.logger.write(f"{Color.YELLOW}[dry-run]{Color.RESET} pediria a URL do repositorio e faria clone/pull")
            self.mark_manual("Dry-run indica solicitacao de URL do repositorio.")
            return
        try:
            repo_url = prompt_user(
                "Informe a URL do repositorio scripts-linux (SSH/HTTPS)",
                self.ctx.logger,
                detail="O clone so continua depois que voce fornecer a URL desejada.",
                prompt_label="Repo URL",
                allow_empty=True,
            ).strip()
        except PromptInterruptedError:
            self.ctx.logger.write(f"{Color.YELLOW}AVISO:{Color.RESET} Entrada da URL interrompida. Clone/pull cancelado.")
            self.mark_skipped("URL do repositorio cancelada pelo usuario.")
            return
        if not repo_url:
            self.ctx.logger.write(f"{Color.YELLOW}AVISO:{Color.RESET} URL vazia, pulando clone.")
            self.mark_skipped("URL do repositorio nao informada.")
            return
        if (target / ".git").exists():
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} repositorio ja existe em {target}; atualizando")
            self.ctx.runner.run(["git", "-C", str(target), "pull", "--ff-only"], check=False, action="Atualizando repositorio scripts-linux")
            self.mark_done("Repositorio scripts-linux atualizado.")
        else:
            self.ctx.runner.run(["git", "clone", repo_url, str(target)], action="Clonando repositorio scripts-linux")
            self.mark_done("Repositorio scripts-linux clonado.")

    def status(self) -> None:
        header(self, self.title, "Verificando git local e estado do repositorio clonado")
        self.ctx.runner.run(["git", "--version"], check=False)
        self.ctx.runner.run(["git", "-C", str(self.ctx.user.home / "repositorios/scripts-linux"), "status", "--short", "--branch"], check=False)
        target = self.ctx.user.home / "repositorios/scripts-linux/.git"
        if target.exists():
            self.mark_applied("Repositorio scripts-linux esta clonado localmente.")
        else:
            self.mark_pending("Repositorio scripts-linux ainda nao foi clonado.", missing=["repositorio scripts-linux"])


class RcloneStep(Step):
    id = "07"
    title = "Google Drive / rclone"
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
            announce(self.ctx.logger, "warning", ".env.local encontrado, mas faltam ID_DO_CLIENTE ou CHAVE_SECRETA_DO_CLIENTE para o rclone.")
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
ExecStart=/usr/bin/notify-send -u critical -i dialog-error "Google Drive nao montado" "Token pode ter expirado. Para corrigir, execute:\\npython -m postformat step 07 apply"
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
            self.ctx.runner.run(["systemctl", "--user", "daemon-reload"], check=False, action="Recarregando servicos do usuario", show_progress=False)
        if self._user_service_active("rclone-google-drive.service") and self._user_service_enabled("rclone-google-drive.service"):
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} rclone-google-drive.service ja esta habilitado e ativo")
        else:
            self.ctx.runner.run(["systemctl", "--user", "enable", "--now", "rclone-google-drive.service"], check=False, action="Habilitando montagem automatica do Google Drive")
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
        remotes = self.ctx.runner.run(["rclone", "listremotes"], check=False, show_progress=False, quiet_success=True, env_extra=rclone_env)
        if remotes and self.remote in (remotes.stdout or ""):
            remote_ready = True
        service_active = self._user_service_active("rclone-google-drive.service")
        service_enabled = self._user_service_enabled("rclone-google-drive.service")
        linger_path = Path(f"/var/lib/systemd/linger/{self.ctx.user.name}")
        linger_enabled = linger_path.exists()
        mount_point = self.ctx.user.home / "GoogleDrive"
        mount_active = mount_point.is_mount()
        if not linger_enabled:
            self.ctx.logger.write(f"{Color.WARNING}Aviso: linger nao habilitado — servico nao inicia no boot sem sessao ativa.{Color.RESET}")
        if not mount_active:
            self.ctx.logger.write(f"{Color.WARNING}Aviso: {mount_point} NAO esta montado.{Color.RESET}")
        if remote_ready and service_active and service_enabled and mount_active and linger_enabled:
            self.mark_applied("Remote, servico e montagem do Google Drive estao ativos e persistentes no boot.")
        elif remote_ready and service_active and service_enabled and mount_active:
            self.mark_attention("Montagem ativa, mas linger desabilitado — nao persiste no boot.", attention=["linger desabilitado"])
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
        self.ctx.runner.run(["systemctl", "daemon-reload"], sudo=True, action="Recarregando systemd apos ajuste do fstab", show_progress=False)
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
        self.ctx.runner.run(["grep", "-n", "pos-formatacao-cachyos\\|/mnt/windows\\|/mnt/dados-windows\\|/mnt/jogos-linux\\|/mnt/backup", "/etc/fstab"], check=False)
        fstab = Path("/etc/fstab")
        text = fstab.read_text(encoding="utf-8", errors="ignore")
        if self.begin in text and self.end in text:
            self.mark_applied("Bloco de montagem persistente esta presente no /etc/fstab.")
        else:
            self.mark_pending("Bloco esperado ainda nao esta presente no /etc/fstab.", missing=["bloco de montagem no fstab"])

    def undo(self) -> None:
        if not self.ctx.runner.dry_run and not confirm_phrase("REMOVER-FSTAB", self.ctx.logger):
            return
        fstab = Path("/etc/fstab")
        backup_existing(fstab, self.ctx.runner, sudo=True)
        write_text_sudo(fstab, self._without_block(fstab.read_text(encoding="utf-8")), self.ctx.runner)
        self.ctx.runner.run(["systemctl", "daemon-reload"], sudo=True, action="Recarregando systemd apos remocao do bloco fstab", show_progress=False)


class GesturesStep(Step):
    id = "09"
    title = "Gestos KDE"

    def apply(self) -> None:
        header(self, self.title, "Instalando e configurando gestos com libinput-gestures")
        if not hardware.has_touchpad():
            self.ctx.logger.write(f"{Color.YELLOW}AVISO:{Color.RESET} nenhum touchpad detectado nesta maquina; gestos nao se aplicam a desktops.")
            self.mark_skipped("Maquina sem touchpad; gestos nao se aplicam.")
            self.mark_applied("Nao aplicavel: maquina sem touchpad.")
            return
        self.ctx.logger.write("Configuracao principal desta etapa: libinput-gestures com gestos de 3 dedos para Overview.")
        install_system_or_aur("libinput-gestures", "libinput-gestures", self.ctx.runner)
        input_group_ready = self._ensure_input_group()
        if not command_exists("libinput-gestures-setup") and not self.ctx.runner.dry_run:
            self.ctx.logger.write("libinput-gestures-setup nao ficou disponivel apos a instalacao.")
            self.mark_manual("libinput-gestures nao ficou disponivel apos a instalacao.")
            return
        self._write_libinput_config()
        self.ctx.runner.run(["libinput-gestures-setup", "autostart"], check=False, action="Ativando autostart do libinput-gestures", show_progress=False)
        self.ctx.runner.run(["libinput-gestures-setup", "restart"], check=False, action="Reiniciando libinput-gestures", show_progress=False)
        if not input_group_ready:
            self.mark_manual(f"Execute 'sudo gpasswd -a {self.ctx.user.name} input' e faca logout/login ou reinicie.")
            return
        self.mark_done("Gestos configurados com libinput-gestures.")

    def _ensure_input_group(self) -> bool:
        if self._user_in_group("input"):
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} usuario {self.ctx.user.name} ja esta no grupo input")
            return True
        result = self.ctx.runner.run(
            ["gpasswd", "-a", self.ctx.user.name, "input"],
            sudo=True,
            check=False,
            action=f"Adicionando {self.ctx.user.name} ao grupo input",
            show_progress=False,
        )
        if result and result.returncode != 0:
            self.ctx.logger.write(
                f"{Color.YELLOW}AVISO:{Color.RESET} nao consegui adicionar {self.ctx.user.name} ao grupo input automaticamente."
            )
            self.ctx.logger.write(f"Execute manualmente: sudo gpasswd -a {self.ctx.user.name} input")
            return False
        self.ctx.logger.write(
            f"{Color.YELLOW}AVISO:{Color.RESET} faca logout/login ou reinicie para o grupo input valer nesta sessao."
        )
        return True

    def _user_in_group(self, group_name: str) -> bool:
        try:
            group = grp.getgrnam(group_name)
        except KeyError:
            return False
        return self.ctx.user.name in group.gr_mem or self.ctx.user.gid == group.gr_gid

    def _write_libinput_config(self) -> None:
        helper = self.ctx.user.home / ".local/bin/kde-gnome-like-overview"
        conf = self.ctx.user.home / ".config/libinput-gestures.conf"
        helper_content = """#!/usr/bin/env bash
qdbus6 org.kde.kglobalaccel /component/kwin org.kde.kglobalaccel.Component.invokeShortcut "Overview" >/dev/null 2>&1 && exit 0
qdbus6 org.kde.KWin /Effects org.kde.kwin.Effects.toggleEffect "overview" >/dev/null 2>&1 && exit 0
exit 1
"""
        conf_content = self._libinput_config_content(helper)
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

    def _libinput_config_content(self, helper: Path) -> str:
        return f"gesture swipe up 3 {helper}\ngesture swipe down 3 {helper}\n"

    def _libinput_config_ready(self, config_file: Path) -> bool:
        if not config_file.exists():
            return False
        helper = self.ctx.user.home / ".local/bin/kde-gnome-like-overview"
        return config_file.read_text(encoding="utf-8", errors="ignore") == self._libinput_config_content(helper)

    def _libinput_gestures_running(self) -> bool:
        if not command_exists("libinput-gestures-setup"):
            return False
        result = self.ctx.runner.run(["libinput-gestures-setup", "status"], check=False)
        output = result.stdout if result and result.stdout else ""
        return "is currently running" in output

    def status(self) -> None:
        header(self, self.title, "Verificando pacote, servico e arquivos de gestos")
        self.ctx.logger.write(f"{badge('desktop', Color.INFO)} {os.environ.get('XDG_CURRENT_DESKTOP', 'desconhecido')}")
        if not hardware.has_touchpad():
            self.ctx.logger.write(f"{badge('touchpad', Color.WARNING)} nenhum touchpad detectado; gestos nao se aplicam a esta maquina.")
            self.mark_applied("Nao aplicavel: maquina sem touchpad.")
            return
        package_ready = system_installed("libinput-gestures")
        group_ready = self._user_in_group("input")
        service_ready = self._libinput_gestures_running()
        self.ctx.logger.write(f"{badge('libinput-gestures', Color.SUCCESS if package_ready else Color.WARNING)} {'instalado' if package_ready else 'ausente'}")
        self.ctx.logger.write(f"{badge('grupo-input', Color.SUCCESS if group_ready else Color.WARNING)} {'ok' if group_ready else 'usuario fora do grupo input'}")
        self.ctx.logger.write(f"{badge('servico', Color.SUCCESS if service_ready else Color.WARNING)} {'rodando' if service_ready else 'parado'}")
        if not command_exists("libinput-gestures-setup"):
            self.ctx.logger.write("libinput-gestures-setup indisponivel.")
        config_file = self.ctx.user.home / ".config/libinput-gestures.conf"
        if config_file.exists():
            self.ctx.runner.run(["cat", str(config_file)], check=False)
        else:
            self.ctx.logger.write(f"Arquivo de configuracao ausente: {config_file}")
        config_ready = config_file.exists()
        expected_config_ready = self._libinput_config_ready(config_file)
        helper_ready = (self.ctx.user.home / ".local/bin/kde-gnome-like-overview").exists()
        if package_ready and group_ready and service_ready and expected_config_ready and helper_ready:
            self.mark_applied("libinput-gestures, grupo input, servico e gestos up/down estao aplicados.")
        elif package_ready or config_ready:
            attention = []
            if not group_ready:
                attention.append("grupo input")
            if not service_ready:
                attention.append("servico libinput-gestures")
            if not expected_config_ready:
                attention.append("gestos up/down")
            if not helper_ready:
                attention.append("helper overview")
            self.mark_attention("Gestos estao parcialmente configurados; revise grupo, servico ou arquivo.", attention=attention)
        else:
            self.mark_pending("libinput-gestures ainda nao esta configurado.", missing=["libinput-gestures", "arquivo de gestos"])

    def undo(self) -> None:
        for path in (self.ctx.user.home / ".config/libinput-gestures.conf", self.ctx.user.home / ".local/bin/kde-gnome-like-overview"):
            if self.ctx.runner.dry_run:
                self.ctx.logger.write(f"{Color.YELLOW}[dry-run]{Color.RESET} removeria {path}")
            else:
                path.unlink(missing_ok=True)
        if command_exists("libinput-gestures-setup"):
            self.ctx.runner.run(["libinput-gestures-setup", "stop"], check=False)
            self.ctx.runner.run(["libinput-gestures-setup", "autostop"], check=False)
        else:
            self.ctx.logger.write("libinput-gestures nao instalado; nada para parar.")


class AppsStep(Step):
    id = "10"
    title = "Apps / jogos / comunicacao / dev"
    hydra_appimage_path = Path("AppImages/HydraLauncher-latest.AppImage")
    hydra_desktop_path = Path(".local/share/applications/hydralauncher.desktop")
    hydra_legacy_desktop_path = Path(".local/share/applications/hydra-launcher.desktop")
    hydra_wm_class = "hydralauncher"
    apps = {
        "Steam": {"system_aliases": ("steam", "steam-installer", "steam-launcher"), "flatpak_id": None, "appimage_paths": (), "desktop_paths": (), "kind": "system"},
        "Heroic": {"system_aliases": ("heroic-games-launcher", "heroic-games-launcher-bin"), "flatpak_id": "com.heroicgameslauncher.hgl", "appimage_paths": (), "desktop_paths": (), "kind": "system"},
        "Discord": {"system_aliases": ("discord",), "flatpak_id": "com.discordapp.Discord", "appimage_paths": (), "desktop_paths": (), "kind": "flatpak"},
        "TeamSpeak": {"system_aliases": ("teamspeak", "teamspeak3"), "flatpak_id": "com.teamspeak.TeamSpeak", "appimage_paths": (), "desktop_paths": (), "kind": "flatpak"},
        "ZapZap": {"system_aliases": ("zapzap",), "flatpak_id": "com.rtosta.zapzap", "appimage_paths": (), "desktop_paths": (), "kind": "system"},
        "ONLYOFFICE": {"system_aliases": ("onlyoffice-desktopeditors",), "flatpak_id": "org.onlyoffice.desktopeditors", "appimage_paths": (), "desktop_paths": (), "kind": "flatpak"},
        "Google Chrome": {"system_aliases": ("google-chrome",), "flatpak_id": "com.google.Chrome", "appimage_paths": (), "desktop_paths": (), "kind": "flatpak"},
        "Minecraft Bedrock Launcher": {"system_aliases": ("mcpelauncher-client", "minecraft-bedrock-launcher"), "flatpak_id": "io.mrarm.mcpelauncher", "appimage_paths": (), "desktop_paths": (), "kind": "flatpak"},
        "Bitwarden": {"system_aliases": ("bitwarden",), "flatpak_id": "com.bitwarden.desktop", "appimage_paths": (), "desktop_paths": (), "kind": "flatpak"},
        "Codex CLI": {"system_aliases": ("nodejs", "npm"), "flatpak_id": None, "appimage_paths": (), "desktop_paths": (), "kind": "cli"},
        "Hydra Launcher": {
            "system_aliases": (),
            "flatpak_id": None,
            "appimage_paths": (hydra_appimage_path,),
            "desktop_paths": (hydra_desktop_path, hydra_legacy_desktop_path),
            "kind": "appimage",
        },
    }

    def apply(self) -> None:
        header(self, self.title, "Instalando apps principais, Hydra e Codex CLI")
        if self._detect_install_source("Steam"):
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} Steam ja detectado via {self._detect_install_source('Steam')}")
        else:
            self._install_steam()
        if self._detect_install_source("Heroic"):
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} Heroic ja detectado via {self._detect_install_source('Heroic')}")
        else:
            self._install_system_or_flatpak("heroic-games-launcher", "heroic-games-launcher-bin", "com.heroicgameslauncher.hgl")
        if self._detect_install_source("ZapZap"):
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} ZapZap ja detectado via {self._detect_install_source('ZapZap')}")
        else:
            self._install_system_or_flatpak("zapzap", "zapzap", "com.rtosta.zapzap")
        self._install_hydra()
        for name, definition in self.apps.items():
            if definition["kind"] != "flatpak":
                continue
            source = self._detect_install_source(name)
            if source:
                self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} {name} ja detectado via {source}")
                continue
            header(self, f"{name} - Flatpak")
            install_flatpak(str(definition["flatpak_id"]), self.ctx.runner)
        header(self, "Codex CLI")
        if self._detect_install_source("Codex CLI"):
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} Codex CLI ja detectado via {self._detect_install_source('Codex CLI')}")
        else:
            install_system_package("nodejs", self.ctx.runner)
            install_system_package("npm", self.ctx.runner)
            if npm_global_installed("@openai/codex"):
                self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} @openai/codex ja instalado globalmente")
            else:
                self.ctx.runner.run(["npm", "install", "-g", "@openai/codex"], sudo=True, action="Instalando Codex CLI globalmente")
        self.mark_done("Apps principais, Hydra e Codex CLI processados.")

    def _install_steam(self) -> None:
        distro = current_distro()
        if install_system_or_aur("steam", "steam", self.ctx.runner):
            return
        if distro.is_debian:
            self.ctx.logger.write(
                f"{Color.YELLOW}AVISO:{Color.RESET} Steam nao apareceu nos repositorios apt atuais. "
                "Habilite multiverse/non-free/non-free-firmware conforme sua distro e rode esta etapa novamente."
            )
            self.mark_manual("Steam depende de repositorios adicionais no Debian/Ubuntu.")

    def _install_system_or_flatpak(self, system_pkg: str, aur_pkg: str | None, flatpak_id: str) -> None:
        if install_system_or_aur(system_pkg, aur_pkg, self.ctx.runner):
            return
        if current_distro().is_debian:
            install_flatpak(flatpak_id, self.ctx.runner)

    def _install_hydra(self) -> None:
        header(self, "Hydra Launcher AppImage", "Baixando AppImage e criando integracao desktop")
        appimage_dir = self.ctx.user.home / "AppImages"
        out = self.ctx.user.home / self.hydra_appimage_path
        icon_source = self.ctx.root / "assets/hydra.png"
        icon_target = self.ctx.user.home / ".local/share/icons/hydra-launcher.png"
        desktop_file = self.ctx.user.home / self.hydra_desktop_path

        if out.exists():
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} Hydra Launcher ja detectado via appimage ({out})")
            self._reconcile_hydra_desktop(out, icon_source, icon_target, desktop_file)
            self.add_hint("Hydra Launcher ja estava instalado.")
            return
        install_system_package("curl", self.ctx.runner)
        if current_distro().is_arch:
            install_first_available(("fuse2",), self.ctx.runner)
        else:
            install_first_available(("libfuse2t64", "libfuse2", "fuse"), self.ctx.runner)
        if not self.ctx.runner.dry_run:
            appimage_dir.mkdir(parents=True, exist_ok=True)
        url_cmd = "curl -fsSL https://api.github.com/repos/hydralauncher/hydra/releases/latest | grep -Eo 'https://[^\\\"]+\\.AppImage' | head -n1"
        result = self.ctx.runner.run(url_cmd, shell=True, check=False, action="Consultando release mais recente do Hydra", show_progress=False)
        url = result.stdout.strip() if result and result.stdout else "HYDRA_APPIMAGE_URL"
        if url == "HYDRA_APPIMAGE_URL" and not self.ctx.runner.dry_run:
            self.ctx.logger.write(f"{Color.YELLOW}AVISO:{Color.RESET} Nao encontrei AppImage do Hydra no release latest.")
            return
        self.ctx.runner.run(["curl", "-L", url, "-o", str(out)], check=False, action="Baixando Hydra Launcher AppImage")
        self.ctx.runner.run(["chmod", "+x", str(out)], check=False, action="Tornando Hydra Launcher executavel", show_progress=False)
        self._reconcile_hydra_desktop(out, icon_source, icon_target, desktop_file)

    def _reconcile_hydra_desktop(self, appimage: Path, icon_source: Path, icon_target: Path, desktop_file: Path) -> None:
        copy_asset(icon_source, icon_target, self.ctx.runner)
        entry = DesktopEntry(
            name="Hydra Launcher",
            exec_line=f"{appimage} %U",
            icon=str(icon_target),
            categories=("Game",),
            startup_wm_class=self.hydra_wm_class,
        )
        install_desktop_entry(desktop_file, entry, self.ctx.runner)
        self._remove_hydra_legacy_desktop(appimage)

    def _remove_hydra_legacy_desktop(self, appimage: Path) -> None:
        legacy_file = self.ctx.user.home / self.hydra_legacy_desktop_path
        canonical_file = self.ctx.user.home / self.hydra_desktop_path
        if not legacy_file.exists() or legacy_file == canonical_file:
            return
        legacy_text = legacy_file.read_text(encoding="utf-8", errors="ignore")
        managed_legacy = str(appimage) in legacy_text and (
            "Name=Hydra Launcher" in legacy_text or "hydra-launcher" in legacy_text or self.hydra_wm_class in legacy_text
        )
        if not managed_legacy:
            self.ctx.logger.write(
                f"{Color.YELLOW}AVISO:{Color.RESET} {legacy_file} parece customizado; preservando para revisao manual."
            )
            return
        if self.ctx.runner.dry_run:
            self.ctx.logger.write(f"{Color.YELLOW}[dry-run]{Color.RESET} removeria atalho legado {legacy_file}")
            return
        legacy_file.unlink()
        self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} Atalho legado removido: {legacy_file}")

    def status(self) -> None:
        header(self, self.title, "Verificando origem detectada de cada app")
        for name in self.apps:
            source = self._detect_install_source(name)
            tone = Color.SUCCESS if source else Color.WARNING
            self.ctx.logger.write(f"{badge(name.lower().replace(' ', '-'), tone)} {name}: {source or 'ausente'}")
        present = [name for name in self.apps if self._detect_install_source(name)]
        missing = [name for name in self.apps if not self._detect_install_source(name)]
        if not missing:
            self.mark_applied("Todos os apps monitorados estao presentes.", items=present)
        elif present:
            self.mark_attention(f"Alguns apps estao presentes e outros faltam: {', '.join(missing)}.", attention=missing)
        else:
            self.mark_pending("Nenhum dos apps monitorados foi detectado.", missing=missing)

    def undo(self) -> None:
        self.ctx.logger.write("Nao vou remover pacotes automaticamente. Removendo apenas Hydra AppImage/atalho/icone criados pela etapa.")
        for path in (
            self.ctx.user.home / "AppImages/HydraLauncher-latest.AppImage",
            self.ctx.user.home / ".local/share/applications/hydralauncher.desktop",
            self.ctx.user.home / ".local/share/applications/hydra-launcher.desktop",
            self.ctx.user.home / ".local/share/icons/hydra-launcher.png",
        ):
            if self.ctx.runner.dry_run:
                self.ctx.logger.write(f"{Color.YELLOW}[dry-run]{Color.RESET} removeria {path}")
            else:
                path.unlink(missing_ok=True)

    def _detect_install_source(self, app_name: str) -> str | None:
        definition = self.apps[app_name]
        for alias in definition["system_aliases"]:
            if system_installed(alias):
                return f"sistema ({alias})"
        flatpak_id = definition["flatpak_id"]
        if flatpak_id and flatpak_installed(str(flatpak_id)):
            return f"flatpak ({flatpak_id})"
        for relative_path in definition["appimage_paths"]:
            if (self.ctx.user.home / relative_path).exists():
                return f"appimage ({self.ctx.user.home / relative_path})"
        for relative_path in definition["desktop_paths"]:
            if (self.ctx.user.home / relative_path).exists():
                return f"desktop ({self.ctx.user.home / relative_path})"
        if app_name == "Codex CLI":
            if command_exists("codex"):
                return "cli (codex no PATH)"
            if npm_global_installed("@openai/codex"):
                return "npm global"
        return None


class NumLockStep(Step):
    id = "11"
    title = "Fixar Num Lock"
    sddm_file = Path("/etc/sddm.conf.d/10-numlock.conf")

    def apply(self) -> None:
        header(self, self.title, "Ajustando Num Lock para sessao e tela de login")
        kde_conf = self.ctx.user.home / ".config/kcminputrc"
        if command_exists("kwriteconfig6"):
            if self._kde_numlock_ready(kde_conf):
                self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} Num Lock do KDE ja esta configurado")
            else:
                backup_existing(kde_conf, self.ctx.runner)
                self.ctx.runner.run(["kwriteconfig6", "--file", "kcminputrc", "--group", "Keyboard", "--key", "NumLock", "0"], check=False, action="Configurando Num Lock do KDE", show_progress=False)
        else:
            content = self._set_ini_value(kde_conf.read_text(encoding="utf-8") if kde_conf.exists() else "", "Keyboard", "NumLock", "0")
            if kde_conf.exists() and kde_conf.read_text(encoding="utf-8", errors="ignore") == content:
                self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} Num Lock do KDE ja esta configurado")
            else:
                backup_existing(kde_conf, self.ctx.runner)
                write_text(kde_conf, content, self.ctx.runner)
        self.ctx.runner.run(["mkdir", "-p", str(self.sddm_file.parent)], sudo=True, action="Garantindo diretorio de configuracao do SDDM", show_progress=False)
        sddm_content = "[General]\nNumlock=on\n"
        if self.sddm_file.exists() and self.sddm_file.read_text(encoding="utf-8", errors="ignore") == sddm_content:
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} Num Lock do SDDM ja esta configurado")
        else:
            backup_existing(self.sddm_file, self.ctx.runner, sudo=True)
            write_text_sudo(self.sddm_file, sddm_content, self.ctx.runner)
        self._warn_sddm_conflicts()
        self.mark_done("Num Lock ajustado para KDE e SDDM.")

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
        header(self, self.title, "Verificando configuracoes atuais de Num Lock")
        self.ctx.runner.run(["grep", "-n", "NumLock", str(self.ctx.user.home / ".config/kcminputrc")], check=False)
        if self.sddm_file.exists():
            self.ctx.runner.run(["cat", str(self.sddm_file)], check=False)
        else:
            self.ctx.logger.write(f"Configuracao SDDM ainda ausente: {self.sddm_file}")
        self.ctx.runner.run(["find", "/etc/sddm.conf.d", "-maxdepth", "1", "-type", "f", "-name", "*.conf"], check=False)
        kde_ready = self._kde_numlock_ready(self.ctx.user.home / ".config/kcminputrc")
        sddm_ready = self.sddm_file.exists()
        if kde_ready and sddm_ready:
            self.mark_applied("Num Lock esta aplicado no KDE e no SDDM.")
        elif kde_ready or sddm_ready:
            self.mark_attention("Num Lock esta aplicado parcialmente; falta KDE ou SDDM.")
        else:
            self.mark_pending("Num Lock ainda nao esta aplicado em KDE/SDDM.", missing=["KDE Num Lock", "SDDM Num Lock"])

    def undo(self) -> None:
        if self.ctx.runner.dry_run:
            self.ctx.logger.write(f"{Color.YELLOW}[dry-run]{Color.RESET} removeria {self.sddm_file}")
        else:
            self.ctx.runner.run(["rm", "-f", str(self.sddm_file)], sudo=True)
        self.ctx.logger.write("A configuracao KDE do usuario foi preservada; use Configuracoes > Teclado para alterar se quiser.")


class AntigravityStep(Step):
    id = "12"
    title = "Google Antigravity IDE"
    url = "https://edgedl.me.gvt1.com/edgedl/release2/j0qc3/antigravity/stable/2.0.4-6381998290370560/linux-x64/Antigravity%20IDE.tar.gz"
    version = "2.0.4-6381998290370560"

    def apply(self) -> None:
        header(self, self.title, "Baixando IDE, integrando desktop e comando de terminal")
        for pkg in ("curl", "tar", "desktop-file-utils", "findutils", "coreutils"):
            install_system_package(pkg, self.ctx.runner)
        cache = self.ctx.user.home / ".cache/antigravity-ide"
        tarball = cache / f"Antigravity-IDE-{self.version}.tar.gz"
        install_dir = self.ctx.user.home / "Antigravity IDE"
        existing_exe = self._find_executable(install_dir) if install_dir.exists() else None
        if existing_exe and self._desktop_ready(existing_exe) and self._wrapper_ready(existing_exe):
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} Antigravity IDE ja instalado e integrado")
            self._path_hint()
            self.mark_skipped("Antigravity IDE ja estava instalado e integrado.")
            return
        if not self.ctx.runner.dry_run:
            cache.mkdir(parents=True, exist_ok=True)
        backup_existing(install_dir, self.ctx.runner)
        if tarball.exists() and tarball.stat().st_size > 1024 * 1024:
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} pacote Antigravity ja esta em cache: {tarball}")
        else:
            self.ctx.runner.run(["curl", "-L", "--fail", "-o", str(tarball), self.url], action="Baixando pacote do Antigravity IDE")
        tmp = self.ctx.user.home / ".cache/antigravity-ide/extract"
        if not self.ctx.runner.dry_run:
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir(parents=True, exist_ok=True)
        self.ctx.runner.run(["tar", "-xzf", str(tarball), "-C", str(tmp)], action="Extraindo pacote do Antigravity IDE")
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
        self.mark_done("Antigravity IDE instalado e integrado.")

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
        install_dir = self.ctx.user.home / "Antigravity IDE"
        desktop_file = self.ctx.user.home / ".local/share/applications/antigravity-ide.desktop"
        wrapper_file = self.ctx.user.home / ".local/bin/antigravity-ide"
        local_bin = str(self.ctx.user.home / ".local/bin")
        path_ready = local_bin in os.environ.get("PATH", "").split(":")
        print_lines(
            self.ctx.logger,
            [
                f"{badge('instalacao', Color.INFO)} {'OK' if install_dir.exists() else 'ausente'} - {install_dir}",
                f"{badge('desktop', Color.INFO)} {'OK' if desktop_file.exists() else 'ausente'} - {desktop_file}",
                f"{badge('wrapper', Color.INFO)} {'OK' if wrapper_file.exists() else 'ausente'} - {wrapper_file}",
                f"{badge('path', Color.SUCCESS if path_ready else Color.WARNING)} {'OK' if path_ready else 'ausente'} - {local_bin}",
            ],
        )
        if install_dir.exists() and desktop_file.exists() and wrapper_file.exists() and path_ready:
            self.mark_applied("Antigravity IDE, desktop, wrapper e PATH estao aplicados.")
        elif wrapper_file.exists() and not path_ready:
            self.mark_attention("Antigravity esta instalado, mas ~/.local/bin ainda nao esta no PATH.")
        else:
            missing = []
            if not install_dir.exists():
                missing.append("instalacao")
            if not desktop_file.exists():
                missing.append("desktop")
            if not wrapper_file.exists():
                missing.append("wrapper")
            self.mark_pending(f"Antigravity ainda nao esta completo: {', '.join(missing)}.", missing=missing)

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


class SunshineStep(Step):
    id = "13"
    title = "Sunshine / Moonlight"
    udev_rule_file = Path("/etc/udev/rules.d/85-sunshine-input.rules")
    udev_rule_content = 'KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"\n'
    ufw_rules = (
        (("47984:47990/tcp", "Sunshine Moonlight TCP"), "47984:47990/tcp"),
        (("48010/tcp", "Sunshine Moonlight RTSP"), "48010/tcp"),
        (("47998:48000/udp", "Sunshine Moonlight UDP"), "47998:48000/udp"),
    )

    @property
    def autostart_file(self) -> Path:
        return self.ctx.user.home / ".config/autostart/sunshine.desktop"

    @property
    def fallback_desktop_file(self) -> Path:
        return self.ctx.user.home / ".local/share/applications/sunshine.desktop"

    @property
    def sunshine_log_file(self) -> Path:
        return self.ctx.user.home / ".local/share/sunshine.log"

    @property
    def user_service_file(self) -> Path:
        return self.ctx.user.home / ".config/systemd/user/sunshine.service"

    def apply(self) -> None:
        header(self, self.title, "Instalando Sunshine e integrando com KDE/Moonlight")
        install_system_package("sunshine", self.ctx.runner)
        input_group_ready = self._ensure_input_group()
        self._write_udev_rule()
        self._write_autostart()
        self._ensure_menu_launcher()
        self._configure_ufw()
        self._start_sunshine()
        if not input_group_ready:
            self.mark_manual(f"Execute logout/login ou reinicie para o grupo input valer para {self.ctx.user.name}.")
            return
        self.mark_done("Sunshine instalado/configurado com autostart, UFW e integracao KDE.")

    def _ensure_input_group(self) -> bool:
        if self._user_in_group("input"):
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} usuario {self.ctx.user.name} ja esta no grupo input")
            return True
        result = self.ctx.runner.run(
            ["gpasswd", "-a", self.ctx.user.name, "input"],
            sudo=True,
            check=False,
            action=f"Adicionando {self.ctx.user.name} ao grupo input",
            show_progress=False,
        )
        if result and result.returncode != 0:
            self.ctx.logger.write(f"{Color.YELLOW}AVISO:{Color.RESET} nao consegui adicionar {self.ctx.user.name} ao grupo input automaticamente.")
            self.ctx.logger.write(f"Execute manualmente: sudo gpasswd -a {self.ctx.user.name} input")
            return False
        self.ctx.logger.write(f"{Color.YELLOW}AVISO:{Color.RESET} faca logout/login ou reinicie para o grupo input valer nesta sessao.")
        return True

    def _user_in_group(self, group_name: str) -> bool:
        try:
            group = grp.getgrnam(group_name)
        except KeyError:
            return False
        return self.ctx.user.name in group.gr_mem or self.ctx.user.gid == group.gr_gid

    def _write_udev_rule(self) -> None:
        if self._udev_rule_ready():
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} regra udev do Sunshine ja esta configurada: {self.udev_rule_file}")
        else:
            backup_existing(self.udev_rule_file, self.ctx.runner, sudo=True)
            write_text_sudo(self.udev_rule_file, self.udev_rule_content, self.ctx.runner)
        self.ctx.runner.run(["udevadm", "control", "--reload-rules"], sudo=True, check=False, action="Recarregando regras udev", show_progress=False)
        self.ctx.runner.run(["udevadm", "trigger"], sudo=True, check=False, action="Aplicando regras udev", show_progress=False)

    def _udev_rule_ready(self) -> bool:
        return self.udev_rule_file.exists() and self.udev_rule_file.read_text(encoding="utf-8", errors="ignore") == self.udev_rule_content

    def _autostart_content(self) -> str:
        return "\n".join(
            [
                "[Desktop Entry]",
                "Type=Application",
                "Name=Sunshine",
                "Comment=Iniciar Sunshine no login do KDE Plasma",
                f'Exec=sh -c "/usr/bin/sunshine > {self.sunshine_log_file} 2>&1"',
                "Terminal=false",
                "X-KDE-autostart-after=panel",
                "X-GNOME-Autostart-enabled=true",
                "",
            ]
        )

    def _write_autostart(self) -> None:
        write_text(self.autostart_file, self._autostart_content(), self.ctx.runner, mode=0o644)
        if self.user_service_file.exists():
            self.ctx.logger.write(f"{Color.YELLOW}AVISO:{Color.RESET} existe sunshine.service de usuario; revise se houver Sunshine duplicado: {self.user_service_file}")

    def _ensure_menu_launcher(self) -> None:
        existing = self._find_existing_launcher()
        if existing and existing != self.fallback_desktop_file:
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} lancador Sunshine ja fornecido pelo sistema: {existing}")
            return
        entry = DesktopEntry(
            name="Sunshine",
            comment="Game streaming host for Moonlight",
            exec_line="/usr/bin/sunshine",
            categories=("Game", "Network"),
            terminal=False,
        )
        install_desktop_entry(self.fallback_desktop_file, entry, self.ctx.runner)

    def _find_existing_launcher(self) -> Path | None:
        fallback_match = None
        search_dirs = (
            self.ctx.user.home / ".local/share/applications",
            Path("/usr/local/share/applications"),
            Path("/usr/share/applications"),
        )
        for directory in search_dirs:
            if not directory.exists():
                continue
            for desktop_file in sorted(directory.glob("*.desktop")):
                if self._desktop_launches_sunshine(desktop_file):
                    if desktop_file == self.fallback_desktop_file:
                        fallback_match = desktop_file
                    else:
                        return desktop_file
        return fallback_match

    def _desktop_launches_sunshine(self, desktop_file: Path) -> bool:
        try:
            text = desktop_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return False
        exec_lines = [line.strip() for line in text.splitlines() if line.strip().startswith("Exec=")]
        return any("/usr/bin/sunshine" in line or re.search(r"(^|[ ='\"])sunshine([ '\"]|$)", line) for line in exec_lines)

    def _ufw_active(self) -> bool:
        if not command_exists("ufw"):
            return False
        result = self.ctx.runner.run(["ufw", "status"], sudo=True, check=False, action="Verificando UFW", show_progress=False, quiet_success=True)
        if self.ctx.runner.dry_run:
            return True
        return bool(result and result.stdout and re.search(r"Status:\s+active", result.stdout, re.IGNORECASE))

    def _configure_ufw(self) -> None:
        if not command_exists("ufw"):
            self.ctx.logger.write(f"{Color.YELLOW}AVISO:{Color.RESET} UFW nao instalado. Pulando regras de firewall.")
            return
        if not self._ufw_active():
            self.ctx.logger.write(f"{Color.YELLOW}AVISO:{Color.RESET} UFW existe, mas nao esta ativo. Pulando regras de firewall.")
            return
        for (port, comment), _delete_spec in self.ufw_rules:
            self.ctx.runner.run(
                ["ufw", "allow", port, "comment", comment],
                sudo=True,
                check=False,
                action=f"Liberando UFW {port} para Sunshine",
                show_progress=False,
            )
        self.ctx.runner.run(["ufw", "reload"], sudo=True, check=False, action="Recarregando UFW", show_progress=False)

    def _sunshine_running(self) -> bool:
        result = self.ctx.runner.run(["pgrep", "-u", self.ctx.user.name, "-x", "sunshine"], check=False, action="Verificando processo Sunshine", show_progress=False, quiet_success=True)
        return bool(result and result.returncode == 0)

    def _start_sunshine(self) -> None:
        if not command_exists("sunshine") and not Path("/usr/bin/sunshine").exists() and not self.ctx.runner.dry_run:
            self.ctx.logger.write(f"{Color.YELLOW}AVISO:{Color.RESET} binario sunshine nao encontrado para iniciar agora.")
            return
        if self._sunshine_running():
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} Sunshine ja esta rodando")
            return
        self.ctx.runner.run(
            f"nohup /usr/bin/sunshine > {self.sunshine_log_file} 2>&1 &",
            shell=True,
            check=False,
            action="Iniciando Sunshine em segundo plano",
            show_progress=False,
        )

    def status(self) -> None:
        header(self, self.title, "Verificando Sunshine, KDE, UFW e portas")
        package_ready = system_installed("sunshine")
        binary_ready = command_exists("sunshine") or Path("/usr/bin/sunshine").exists()
        process_ready = self._sunshine_running()
        group_ready = self._user_in_group("input")
        udev_ready = self._udev_rule_ready()
        autostart_ready = self.autostart_file.exists() and self.autostart_file.read_text(encoding="utf-8", errors="ignore") == self._autostart_content()
        launcher = self._find_existing_launcher()
        launcher_ready = launcher is not None
        service_conflict = self.user_service_file.exists()
        ufw_ready = self._ufw_rules_ready()

        print_lines(
            self.ctx.logger,
            [
                f"{badge('pacote', Color.SUCCESS if package_ready else Color.WARNING)} {'instalado' if package_ready else 'ausente'}",
                f"{badge('binario', Color.SUCCESS if binary_ready else Color.WARNING)} {'OK' if binary_ready else 'ausente'}",
                f"{badge('processo', Color.SUCCESS if process_ready else Color.WARNING)} {'rodando' if process_ready else 'parado'}",
                f"{badge('grupo-input', Color.SUCCESS if group_ready else Color.WARNING)} {'OK' if group_ready else 'usuario fora do grupo input'}",
                f"{badge('udev', Color.SUCCESS if udev_ready else Color.WARNING)} {'OK' if udev_ready else 'regra ausente/diferente'} - {self.udev_rule_file}",
                f"{badge('autostart', Color.SUCCESS if autostart_ready else Color.WARNING)} {'OK' if autostart_ready else 'ausente/diferente'} - {self.autostart_file}",
                f"{badge('launcher', Color.SUCCESS if launcher_ready else Color.WARNING)} {launcher or 'ausente'}",
                f"{badge('ufw', Color.SUCCESS if ufw_ready else Color.WARNING)} {'regras OK ou UFW inativo/ausente' if ufw_ready else 'UFW ativo sem todas as regras'}",
                f"{badge('web', Color.INFO)} https://localhost:47990",
            ],
        )
        if service_conflict:
            self.ctx.logger.write(f"{badge('atencao', Color.WARNING)} sunshine.service de usuario existe: {self.user_service_file}")
        self._print_ufw_status()
        self._print_listening_ports()

        required_ready = package_ready and binary_ready and group_ready and udev_ready and autostart_ready and launcher_ready and ufw_ready
        if required_ready and not service_conflict:
            self.mark_applied("Sunshine, permissoes, autostart, launcher e UFW estao aplicados.")
        elif required_ready and service_conflict:
            self.mark_attention("Sunshine esta configurado, mas existe sunshine.service de usuario que pode duplicar o autostart.", attention=["sunshine.service de usuario"])
        else:
            missing = []
            if not package_ready:
                missing.append("pacote sunshine")
            if not group_ready:
                missing.append("grupo input")
            if not udev_ready:
                missing.append("regra udev")
            if not autostart_ready:
                missing.append("autostart KDE")
            if not launcher_ready:
                missing.append("launcher desktop")
            if not ufw_ready:
                missing.append("regras UFW")
            self.mark_pending(f"Sunshine ainda nao esta completo: {', '.join(missing)}.", missing=missing)

    def _ufw_rules_ready(self) -> bool:
        if not command_exists("ufw"):
            return True
        result = self.ctx.runner.run(["ufw", "status"], sudo=True, check=False, action="Verificando regras UFW do Sunshine", show_progress=False, quiet_success=True)
        output = result.stdout if result and result.stdout else ""
        if not re.search(r"Status:\s+active", output, re.IGNORECASE):
            return True
        return all(spec in output for _rule, spec in self.ufw_rules)

    def _print_ufw_status(self) -> None:
        if command_exists("ufw"):
            self.ctx.runner.run(["ufw", "status", "verbose"], sudo=True, check=False, action="Status detalhado do UFW", show_progress=False)
        else:
            self.ctx.logger.write("UFW nao instalado.")

    def _print_listening_ports(self) -> None:
        if command_exists("ss"):
            self.ctx.runner.run(
                "ss -lntup 2>/dev/null | grep -E '47984|47989|47990|48010|47998|47999|48000' || true",
                shell=True,
                check=False,
                action="Verificando portas Sunshine em escuta",
                show_progress=False,
            )
        else:
            self.ctx.logger.write("Comando ss nao encontrado.")

    def undo(self) -> None:
        self.ctx.logger.write("Undo remove autostart, fallback desktop, regra udev gerenciada e regras UFW. Pacote e configuracao interna do Sunshine sao preservados.")
        self.ctx.runner.run(["pkill", "-u", self.ctx.user.name, "-x", "sunshine"], check=False, action="Parando processo Sunshine", show_progress=False)
        self._remove_user_file(self.autostart_file)
        self._remove_fallback_desktop_if_managed()
        self._remove_udev_rule_if_managed()
        self._remove_ufw_rules()

    def _remove_user_file(self, path: Path) -> None:
        if self.ctx.runner.dry_run:
            self.ctx.logger.write(f"{Color.YELLOW}[dry-run]{Color.RESET} removeria {path}")
        else:
            path.unlink(missing_ok=True)

    def _remove_fallback_desktop_if_managed(self) -> None:
        if not self.fallback_desktop_file.exists():
            if self.ctx.runner.dry_run:
                self.ctx.logger.write(f"{Color.YELLOW}[dry-run]{Color.RESET} removeria fallback desktop se existisse: {self.fallback_desktop_file}")
            return
        if self._desktop_launches_sunshine(self.fallback_desktop_file):
            self._remove_user_file(self.fallback_desktop_file)
        else:
            self.ctx.logger.write(f"{Color.YELLOW}AVISO:{Color.RESET} fallback desktop parece customizado; preservando: {self.fallback_desktop_file}")

    def _remove_udev_rule_if_managed(self) -> None:
        if self.ctx.runner.dry_run:
            self.ctx.logger.write(f"{Color.YELLOW}[dry-run]{Color.RESET} removeria {self.udev_rule_file} se contiver a regra gerenciada")
            return
        if not self.udev_rule_file.exists():
            return
        if self.udev_rule_file.read_text(encoding="utf-8", errors="ignore") == self.udev_rule_content:
            self.ctx.runner.run(["rm", "-f", str(self.udev_rule_file)], sudo=True, check=False, action="Removendo regra udev do Sunshine", show_progress=False)
            self.ctx.runner.run(["udevadm", "control", "--reload-rules"], sudo=True, check=False, action="Recarregando regras udev", show_progress=False)
        else:
            self.ctx.logger.write(f"{Color.YELLOW}AVISO:{Color.RESET} regra udev parece customizada; preservando: {self.udev_rule_file}")

    def _remove_ufw_rules(self) -> None:
        if not command_exists("ufw"):
            self.ctx.logger.write("UFW nao instalado; nada para remover.")
            return
        if not self._ufw_active():
            self.ctx.logger.write("UFW inativo; regras nao serao removidas.")
            return
        for _rule, delete_spec in self.ufw_rules:
            self.ctx.runner.run(["ufw", "delete", "allow", delete_spec], sudo=True, check=False, action=f"Removendo regra UFW {delete_spec}", show_progress=False)
        self.ctx.runner.run(["ufw", "reload"], sudo=True, check=False, action="Recarregando UFW", show_progress=False)


class HardwareStep(Step):
    id = "14"
    title = "Inventario de Hardware"

    @property
    def report_file(self) -> Path:
        return hardware.report_path(self.ctx.user)

    def apply(self) -> None:
        header(self, self.title, "Coletando informacoes de hardware e salvando relatorio")
        self._ensure_tools()
        destino = hardware.collect_report(self.ctx)
        if self.ctx.runner.dry_run:
            self.mark_done("Coleta simulada (dry-run); nenhum relatorio gravado.")
            return
        facts = hardware.read_facts()
        self.ctx.logger.write("")
        for line in hardware.facts_summary(facts):
            self.ctx.logger.write(f"{Color.GREEN}-{Color.RESET} {line}")
        self.ctx.logger.write("")
        self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} relatorio salvo em {destino}")
        self.add_hint(f"Compartilhe o arquivo quando precisar de suporte: {destino}")
        self.mark_done(f"Inventario coletado e salvo em {destino}.")
        self.mark_applied("Inventario de hardware coletado.", items=hardware.facts_summary(facts))

    def _ensure_tools(self) -> None:
        # Ferramentas opcionais que enriquecem o relatorio; nao travam se faltarem.
        if not command_exists("dmidecode"):
            install_system_package("dmidecode", self.ctx.runner)
        if not command_exists("inxi"):
            install_system_or_aur("inxi", "inxi", self.ctx.runner)

    def status(self) -> None:
        header(self, self.title, "Verificando ultimo inventario de hardware coletado")
        destino = self.report_file
        if not destino.exists():
            self.ctx.logger.write(f"Nenhum inventario coletado ainda em {destino}")
            self.mark_pending("Nenhum inventario de hardware foi coletado ainda.", missing=[str(destino)])
            return
        mtime = datetime.fromtimestamp(destino.stat().st_mtime)
        self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} relatorio em {destino}")
        self.ctx.logger.write(f"Ultima coleta: {mtime:%Y-%m-%d %H:%M:%S}")
        facts = hardware.read_facts()
        for line in hardware.facts_summary(facts):
            self.ctx.logger.write(f"  - {line}")
        self.mark_applied(f"Inventario disponivel (coletado em {mtime:%Y-%m-%d %H:%M}).")

    def undo(self) -> None:
        destino = self.report_file
        if self.ctx.runner.dry_run:
            self.ctx.logger.write(f"{Color.YELLOW}[dry-run]{Color.RESET} removeria {destino}")
            return
        if destino.exists():
            self.ctx.runner.run(["rm", "-f", str(destino)], check=False, action="Removendo inventario de hardware", show_progress=False)
        else:
            self.ctx.logger.write(f"Nada para remover; {destino} nao existe.")


ALL_STEPS: tuple[type[Step], ...] = (
    ShellyStep,
    UpdateSystemStep,
    LinuxToysStep,
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
    SunshineStep,
    HardwareStep,
)
