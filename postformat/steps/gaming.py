from __future__ import annotations

import grp
import os
import re
import subprocess
from pathlib import Path

from .. import hardware
from ..core import (
    Color,
    announce,
    backup_existing,
    badge,
    command_exists,
    paint,
    print_lines,
    write_text,
    write_text_sudo,
)
from ..desktop import DesktopEntry, install_desktop_entry
from ..installers import (
    copy_asset,
    ensure_rpmfusion,
    flatpak_installed,
    install_first_available,
    install_flatpak,
    install_system_or_aur,
    install_system_package,
    npm_global_installed,
    system_installed,
)
from ..platform import current_distro
from ..steps_base import Step
from ._common import ProbeResult, header


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
            return ProbeResult(
                "OpenGL (GPU primaria)",
                "problem",
                "glxinfo nao esta instalado.",
                "Instale mesa-utils para validar o OpenGL.",
            )
        result = self._run_probe(["glxinfo", "-B"], "Consultando capacidades OpenGL")
        output = self._combined_output(result).lower()
        if (
            result.returncode == 0
            and "direct rendering: yes" in output
            and ("opengl renderer string:" in output or "vendor:" in output)
        ):
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
                return ProbeResult(
                    "GPU NVIDIA dedicada",
                    "ok",
                    "nao aplicavel: maquina com GPU unica (prime-run e para notebooks hibridos).",
                )
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
            summary = (
                f"nvidia-smi respondeu corretamente ({gpu_name})." if gpu_name else "nvidia-smi respondeu corretamente."
            )
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
            self.ctx.logger.write(
                f"{badge(label_map[item.status], tone_map[item.status])} {item.label}: {item.summary}"
            )
            if item.details:
                self.ctx.logger.write(paint(f"Detalhes: {item.details}", Color.MUTED))
        problem_count = sum(1 for item in results if item.status == "problem")
        warn_count = sum(1 for item in results if item.status == "warn")
        if problem_count == 0 and warn_count == 0:
            announce(self.ctx.logger, "done", "Tudo certo com sessao grafica, GPUs e launchers avaliados.")
        elif problem_count == 0:
            announce(
                self.ctx.logger,
                "warning",
                f"Validacao parcialmente pronta: {warn_count} alerta(s), sem falhas criticas.",
            )
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


class AppsStep(Step):
    id = "10"
    title = "Apps / jogos / comunicacao / dev"
    hydra_appimage_path = Path("AppImages/HydraLauncher-latest.AppImage")
    hydra_desktop_path = Path(".local/share/applications/hydralauncher.desktop")
    hydra_legacy_desktop_path = Path(".local/share/applications/hydra-launcher.desktop")
    hydra_wm_class = "hydralauncher"
    apps = {
        "Steam": {
            "system_aliases": ("steam", "steam-installer", "steam-launcher"),
            "flatpak_id": None,
            "appimage_paths": (),
            "desktop_paths": (),
            "kind": "system",
        },
        "Heroic": {
            "system_aliases": ("heroic-games-launcher", "heroic-games-launcher-bin"),
            "flatpak_id": "com.heroicgameslauncher.hgl",
            "appimage_paths": (),
            "desktop_paths": (),
            "kind": "system",
        },
        "Discord": {
            "system_aliases": ("discord",),
            "flatpak_id": "com.discordapp.Discord",
            "appimage_paths": (),
            "desktop_paths": (),
            "kind": "flatpak",
        },
        "TeamSpeak": {
            "system_aliases": ("teamspeak", "teamspeak3"),
            "flatpak_id": "com.teamspeak.TeamSpeak",
            "appimage_paths": (),
            "desktop_paths": (),
            "kind": "flatpak",
        },
        "ZapZap": {
            "system_aliases": ("zapzap",),
            "flatpak_id": "com.rtosta.zapzap",
            "appimage_paths": (),
            "desktop_paths": (),
            "kind": "system",
        },
        "ONLYOFFICE": {
            "system_aliases": ("onlyoffice-desktopeditors",),
            "flatpak_id": "org.onlyoffice.desktopeditors",
            "appimage_paths": (),
            "desktop_paths": (),
            "kind": "flatpak",
        },
        "Google Chrome": {
            "system_aliases": ("google-chrome",),
            "flatpak_id": "com.google.Chrome",
            "appimage_paths": (),
            "desktop_paths": (),
            "kind": "flatpak",
        },
        "Minecraft Bedrock Launcher": {
            "system_aliases": ("mcpelauncher-client", "minecraft-bedrock-launcher"),
            "flatpak_id": "io.mrarm.mcpelauncher",
            "appimage_paths": (),
            "desktop_paths": (),
            "kind": "flatpak",
        },
        "Bitwarden": {
            "system_aliases": ("bitwarden",),
            "flatpak_id": "com.bitwarden.desktop",
            "appimage_paths": (),
            "desktop_paths": (),
            "kind": "flatpak",
        },
        "Codex CLI": {
            "system_aliases": ("nodejs", "npm"),
            "flatpak_id": None,
            "appimage_paths": (),
            "desktop_paths": (),
            "kind": "cli",
        },
        "auto-cpufreq": {
            "system_aliases": ("auto-cpufreq",),
            "flatpak_id": None,
            "appimage_paths": (),
            "desktop_paths": (),
            "kind": "system",
        },
        "Solaar": {
            "system_aliases": ("solaar",),
            "flatpak_id": "io.github.pwr_solaar.solaar",
            "appimage_paths": (),
            "desktop_paths": (),
            "kind": "system",
        },
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
            self.ctx.logger.write(
                f"{Color.GREEN}OK:{Color.RESET} Steam ja detectado via {self._detect_install_source('Steam')}"
            )
        else:
            self._install_steam()
        if self._detect_install_source("Heroic"):
            self.ctx.logger.write(
                f"{Color.GREEN}OK:{Color.RESET} Heroic ja detectado via {self._detect_install_source('Heroic')}"
            )
        else:
            self._install_system_or_flatpak(
                "heroic-games-launcher", "heroic-games-launcher-bin", "com.heroicgameslauncher.hgl"
            )
        if self._detect_install_source("ZapZap"):
            self.ctx.logger.write(
                f"{Color.GREEN}OK:{Color.RESET} ZapZap ja detectado via {self._detect_install_source('ZapZap')}"
            )
        else:
            self._install_system_or_flatpak("zapzap", "zapzap", "com.rtosta.zapzap")
        if self._detect_install_source("Solaar"):
            self.ctx.logger.write(
                f"{Color.GREEN}OK:{Color.RESET} Solaar ja detectado via {self._detect_install_source('Solaar')}"
            )
        else:
            self._install_solaar()
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
            self.ctx.logger.write(
                f"{Color.GREEN}OK:{Color.RESET} Codex CLI ja detectado via {self._detect_install_source('Codex CLI')}"
            )
        else:
            install_system_package("nodejs", self.ctx.runner)
            install_system_package("npm", self.ctx.runner)
            if npm_global_installed("@openai/codex"):
                self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} @openai/codex ja instalado globalmente")
            else:
                self.ctx.runner.run(
                    ["npm", "install", "-g", "@openai/codex"], sudo=True, action="Instalando Codex CLI globalmente"
                )
        self._install_auto_cpufreq()
        self.mark_done("Apps principais, Hydra, Codex CLI e auto-cpufreq processados.")

    def _install_auto_cpufreq(self) -> None:
        source = self._detect_install_source("auto-cpufreq")
        if source:
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} auto-cpufreq ja detectado via {source}")
            self._enable_auto_cpufreq_daemon()
            return
        header(self, "auto-cpufreq", "Instalando gerenciador automatico de CPU")
        if install_system_or_aur("auto-cpufreq", "auto-cpufreq", self.ctx.runner):
            self._enable_auto_cpufreq_daemon()
            return
        if current_distro().immutable:
            self.ctx.logger.write(
                f"{Color.YELLOW}AVISO:{Color.RESET} sistema imutavel: o instalador do auto-cpufreq escreve em /usr "
                "(somente leitura). Pulando instalacao via GitHub."
            )
            self.add_hint("auto-cpufreq nao foi instalado: sistema imutavel sem pacote nativo disponivel.")
            return
        self.ctx.logger.write(
            f"{Color.YELLOW}AVISO:{Color.RESET} auto-cpufreq nao possui Flatpak oficial; usando instalador do GitHub."
        )
        install_system_package("git", self.ctx.runner)
        src_dir = self.ctx.user.home / ".cache/auto-cpufreq-src"
        if src_dir.exists():
            self.ctx.runner.run(
                ["git", "-C", str(src_dir), "pull", "--ff-only"],
                check=False,
                action="Atualizando codigo do auto-cpufreq",
            )
        else:
            if not self.ctx.runner.dry_run:
                src_dir.parent.mkdir(parents=True, exist_ok=True)
            self.ctx.runner.run(
                ["git", "clone", "https://github.com/AdnanHodzic/auto-cpufreq.git", str(src_dir)],
                check=False,
                action="Clonando auto-cpufreq do GitHub",
            )
        self.ctx.runner.run(
            ["./auto-cpufreq-installer"],
            sudo=True,
            cwd=src_dir,
            check=False,
            interactive=True,
            interactive_tty=True,
            manual_message="Instalador interativo do auto-cpufreq: confirme com 'i' quando solicitado.",
            action="Instalando auto-cpufreq via GitHub",
        )
        self._enable_auto_cpufreq_daemon()

    def _enable_auto_cpufreq_daemon(self) -> None:
        if not command_exists("auto-cpufreq") and not self.ctx.runner.dry_run:
            self.ctx.logger.write(
                f"{Color.YELLOW}AVISO:{Color.RESET} binario auto-cpufreq nao encontrado para habilitar o daemon."
            )
            return
        self.ctx.runner.run(
            ["auto-cpufreq", "--install"],
            sudo=True,
            check=False,
            interactive=True,
            interactive_tty=True,
            manual_message="auto-cpufreq --install pode pedir confirmacao/senha sudo.",
            action="Habilitando daemon auto-cpufreq (systemd)",
        )

    def _install_steam(self) -> None:
        distro = current_distro()
        if distro.immutable and (
            command_exists("steam") or any(system_installed(p) for p in ("steam", "steam-installer", "steam-launcher"))
        ):
            self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} Steam ja disponivel no sistema.")
            return
        if distro.is_fedora and not distro.immutable:
            ensure_rpmfusion(self.ctx.runner)
        if install_system_or_aur("steam", "steam", self.ctx.runner):
            return
        if distro.is_debian:
            self.ctx.logger.write(
                f"{Color.YELLOW}AVISO:{Color.RESET} Steam nao apareceu nos repositorios apt atuais. "
                "Habilite multiverse/non-free/non-free-firmware conforme sua distro e rode esta etapa novamente."
            )
            self.mark_manual("Steam depende de repositorios adicionais no Debian/Ubuntu.")
            return
        # Fedora sem RPM Fusion, imutaveis ou repos sem Steam: caimos no Flatpak.
        install_flatpak("com.valvesoftware.Steam", self.ctx.runner)

    def _install_solaar(self) -> None:
        header(self, "Solaar", "Gerenciador de dispositivos Logitech")
        if install_system_or_aur("solaar", "solaar", self.ctx.runner):
            return
        install_flatpak("io.github.pwr_solaar.solaar", self.ctx.runner)
        # Em dry-run nada e instalado de fato; nao acionar o fallback.
        if self.ctx.runner.dry_run or flatpak_installed("io.github.pwr_solaar.solaar"):
            return
        self.ctx.logger.write(
            f"{Color.YELLOW}AVISO:{Color.RESET} Solaar indisponivel nos repositorios; "
            "instalando Piper como alternativa."
        )
        header(self, "Piper", "Alternativa ao Solaar para mouses gaming")
        if install_system_or_aur("piper", "piper", self.ctx.runner):
            return
        install_flatpak("org.freedesktop.Piper", self.ctx.runner)

    def _install_system_or_flatpak(self, system_pkg: str, aur_pkg: str | None, flatpak_id: str) -> None:
        if install_system_or_aur(system_pkg, aur_pkg, self.ctx.runner):
            return
        # Qualquer familia (incluindo imutaveis) cai no Flatpak quando o nativo nao instala.
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
        distro = current_distro()
        if distro.is_arch:
            install_first_available(("fuse2",), self.ctx.runner)
        elif distro.is_fedora:
            install_first_available(("fuse", "fuse-libs"), self.ctx.runner)
        else:
            install_first_available(("libfuse2t64", "libfuse2", "fuse"), self.ctx.runner)
        if not self.ctx.runner.dry_run:
            appimage_dir.mkdir(parents=True, exist_ok=True)
        url_cmd = "curl -fsSL https://api.github.com/repos/hydralauncher/hydra/releases/latest | grep -Eo 'https://[^\\\"]+\\.AppImage' | head -n1"
        result = self.ctx.runner.run(
            url_cmd, shell=True, check=False, action="Consultando release mais recente do Hydra", show_progress=False
        )
        url = result.stdout.strip() if result and result.stdout else "HYDRA_APPIMAGE_URL"
        if url == "HYDRA_APPIMAGE_URL" and not self.ctx.runner.dry_run:
            self.ctx.logger.write(
                f"{Color.YELLOW}AVISO:{Color.RESET} Nao encontrei AppImage do Hydra no release latest."
            )
            return
        self.ctx.runner.run(["curl", "-L", url, "-o", str(out)], check=False, action="Baixando Hydra Launcher AppImage")
        self.ctx.runner.run(
            ["chmod", "+x", str(out)], check=False, action="Tornando Hydra Launcher executavel", show_progress=False
        )
        self._reconcile_hydra_desktop(out, icon_source, icon_target, desktop_file)

    def _reconcile_hydra_desktop(
        self, appimage: Path, icon_source: Path, icon_target: Path, desktop_file: Path
    ) -> None:
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
            "Name=Hydra Launcher" in legacy_text
            or "hydra-launcher" in legacy_text
            or self.hydra_wm_class in legacy_text
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
            self.mark_attention(
                f"Alguns apps estao presentes e outros faltam: {', '.join(missing)}.", attention=missing
            )
        else:
            self.mark_pending("Nenhum dos apps monitorados foi detectado.", missing=missing)

    def undo(self) -> None:
        self.ctx.logger.write(
            "Nao vou remover pacotes automaticamente. Removendo apenas Hydra AppImage/atalho/icone criados pela etapa."
        )
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
        if app_name == "auto-cpufreq" and command_exists("auto-cpufreq"):
            return "cli (auto-cpufreq no PATH)"
        return None


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

    def _write_udev_rule(self) -> None:
        if self._udev_rule_ready():
            self.ctx.logger.write(
                f"{Color.GREEN}OK:{Color.RESET} regra udev do Sunshine ja esta configurada: {self.udev_rule_file}"
            )
        else:
            backup_existing(self.udev_rule_file, self.ctx.runner, sudo=True)
            write_text_sudo(self.udev_rule_file, self.udev_rule_content, self.ctx.runner)
        self.ctx.runner.run(
            ["udevadm", "control", "--reload-rules"],
            sudo=True,
            check=False,
            action="Recarregando regras udev",
            show_progress=False,
        )
        self.ctx.runner.run(
            ["udevadm", "trigger"], sudo=True, check=False, action="Aplicando regras udev", show_progress=False
        )

    def _udev_rule_ready(self) -> bool:
        return (
            self.udev_rule_file.exists()
            and self.udev_rule_file.read_text(encoding="utf-8", errors="ignore") == self.udev_rule_content
        )

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
            self.ctx.logger.write(
                f"{Color.YELLOW}AVISO:{Color.RESET} existe sunshine.service de usuario; revise se houver Sunshine duplicado: {self.user_service_file}"
            )

    def _ensure_menu_launcher(self) -> None:
        existing = self._find_existing_launcher()
        if existing and existing != self.fallback_desktop_file:
            self.ctx.logger.write(
                f"{Color.GREEN}OK:{Color.RESET} lancador Sunshine ja fornecido pelo sistema: {existing}"
            )
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
        return any(
            "/usr/bin/sunshine" in line or re.search(r"(^|[ ='\"])sunshine([ '\"]|$)", line) for line in exec_lines
        )

    def _ufw_active(self) -> bool:
        if not command_exists("ufw"):
            return False
        result = self.ctx.runner.run(
            ["ufw", "status"], sudo=True, check=False, action="Verificando UFW", show_progress=False, quiet_success=True
        )
        if self.ctx.runner.dry_run:
            return True
        return bool(result and result.stdout and re.search(r"Status:\s+active", result.stdout, re.IGNORECASE))

    def _configure_ufw(self) -> None:
        if not command_exists("ufw"):
            self.ctx.logger.write(f"{Color.YELLOW}AVISO:{Color.RESET} UFW nao instalado. Pulando regras de firewall.")
            return
        if not self._ufw_active():
            self.ctx.logger.write(
                f"{Color.YELLOW}AVISO:{Color.RESET} UFW existe, mas nao esta ativo. Pulando regras de firewall."
            )
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
        result = self.ctx.runner.run(
            ["pgrep", "-u", self.ctx.user.name, "-x", "sunshine"],
            check=False,
            action="Verificando processo Sunshine",
            show_progress=False,
            quiet_success=True,
        )
        return bool(result and result.returncode == 0)

    def _start_sunshine(self) -> None:
        if not command_exists("sunshine") and not Path("/usr/bin/sunshine").exists() and not self.ctx.runner.dry_run:
            self.ctx.logger.write(
                f"{Color.YELLOW}AVISO:{Color.RESET} binario sunshine nao encontrado para iniciar agora."
            )
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
        autostart_ready = (
            self.autostart_file.exists()
            and self.autostart_file.read_text(encoding="utf-8", errors="ignore") == self._autostart_content()
        )
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
            self.ctx.logger.write(
                f"{badge('atencao', Color.WARNING)} sunshine.service de usuario existe: {self.user_service_file}"
            )
        self._print_ufw_status()
        self._print_listening_ports()

        required_ready = (
            package_ready
            and binary_ready
            and group_ready
            and udev_ready
            and autostart_ready
            and launcher_ready
            and ufw_ready
        )
        if required_ready and not service_conflict:
            self.mark_applied("Sunshine, permissoes, autostart, launcher e UFW estao aplicados.")
        elif required_ready and service_conflict:
            self.mark_attention(
                "Sunshine esta configurado, mas existe sunshine.service de usuario que pode duplicar o autostart.",
                attention=["sunshine.service de usuario"],
            )
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
        result = self.ctx.runner.run(
            ["ufw", "status"],
            sudo=True,
            check=False,
            action="Verificando regras UFW do Sunshine",
            show_progress=False,
            quiet_success=True,
        )
        output = result.stdout if result and result.stdout else ""
        if not re.search(r"Status:\s+active", output, re.IGNORECASE):
            return True
        return all(spec in output for _rule, spec in self.ufw_rules)

    def _print_ufw_status(self) -> None:
        if command_exists("ufw"):
            self.ctx.runner.run(
                ["ufw", "status", "verbose"],
                sudo=True,
                check=False,
                action="Status detalhado do UFW",
                show_progress=False,
            )
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
        self.ctx.logger.write(
            "Undo remove autostart, fallback desktop, regra udev gerenciada e regras UFW. Pacote e configuracao interna do Sunshine sao preservados."
        )
        self.ctx.runner.run(
            ["pkill", "-u", self.ctx.user.name, "-x", "sunshine"],
            check=False,
            action="Parando processo Sunshine",
            show_progress=False,
        )
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
                self.ctx.logger.write(
                    f"{Color.YELLOW}[dry-run]{Color.RESET} removeria fallback desktop se existisse: {self.fallback_desktop_file}"
                )
            return
        if self._desktop_launches_sunshine(self.fallback_desktop_file):
            self._remove_user_file(self.fallback_desktop_file)
        else:
            self.ctx.logger.write(
                f"{Color.YELLOW}AVISO:{Color.RESET} fallback desktop parece customizado; preservando: {self.fallback_desktop_file}"
            )

    def _remove_udev_rule_if_managed(self) -> None:
        if self.ctx.runner.dry_run:
            self.ctx.logger.write(
                f"{Color.YELLOW}[dry-run]{Color.RESET} removeria {self.udev_rule_file} se contiver a regra gerenciada"
            )
            return
        if not self.udev_rule_file.exists():
            return
        if self.udev_rule_file.read_text(encoding="utf-8", errors="ignore") == self.udev_rule_content:
            self.ctx.runner.run(
                ["rm", "-f", str(self.udev_rule_file)],
                sudo=True,
                check=False,
                action="Removendo regra udev do Sunshine",
                show_progress=False,
            )
            self.ctx.runner.run(
                ["udevadm", "control", "--reload-rules"],
                sudo=True,
                check=False,
                action="Recarregando regras udev",
                show_progress=False,
            )
        else:
            self.ctx.logger.write(
                f"{Color.YELLOW}AVISO:{Color.RESET} regra udev parece customizada; preservando: {self.udev_rule_file}"
            )

    def _remove_ufw_rules(self) -> None:
        if not command_exists("ufw"):
            self.ctx.logger.write("UFW nao instalado; nada para remover.")
            return
        if not self._ufw_active():
            self.ctx.logger.write("UFW inativo; regras nao serao removidas.")
            return
        for _rule, delete_spec in self.ufw_rules:
            self.ctx.runner.run(
                ["ufw", "delete", "allow", delete_spec],
                sudo=True,
                check=False,
                action=f"Removendo regra UFW {delete_spec}",
                show_progress=False,
            )
        self.ctx.runner.run(["ufw", "reload"], sudo=True, check=False, action="Recarregando UFW", show_progress=False)
