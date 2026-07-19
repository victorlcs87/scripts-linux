from __future__ import annotations

import os
import re
import subprocess
from functools import partial
from pathlib import Path

from .. import hardware
from ..core import (
    Color,
    announce,
    backup_existing,
    badge,
    capture,
    command_exists,
    confirm_phrase,
    paint,
    print_lines,
    write_text,
    write_text_sudo,
)
from ..desktop import DesktopEntry, install_desktop_entry
from ..installers import (
    flatpak_installed,
    install_flatpak,
    install_flatpak_or_system,
    install_system_or_flatpak,
    npm_global_installed,
    remove_flatpak,
)
from ..platform import (
    current_distro,
    ensure_rpmfusion,
    install_system_or_aur,
    install_system_package,
    installed_packages_matching,
    remove_system_packages,
    system_installed,
)
from ..steps_base import Step, StepTask
from ._common import InputGroupMixin, ProbeResult, header

# Pacotes de driver por fabricante e familia de distro. Em Arch, os `lib32-*`
# dependem do repositorio [multilib] habilitado (padrao no CachyOS).
_AMD_INSTALL: dict[str, list[str]] = {
    "arch": [
        "mesa",
        "lib32-mesa",
        "vulkan-radeon",
        "lib32-vulkan-radeon",
        "libva-mesa-driver",
        "lib32-libva-mesa-driver",
        "mesa-vdpau",
        "lib32-mesa-vdpau",
        "vulkan-tools",
        "mesa-utils",
    ],
    "fedora": [
        "mesa-vulkan-drivers",
        "mesa-vulkan-drivers.i686",
        "mesa-va-drivers",
        "mesa-vdpau-drivers",
        "vulkan-tools",
        "glx-utils",
    ],
    "debian": [
        "mesa-vulkan-drivers",
        "mesa-va-drivers",
        "mesa-vdpau-drivers",
        "vulkan-tools",
        "mesa-utils",
    ],
}

# Pacotes AMD-especificos que o step gerencia/remove. Nao inclui `mesa` (generico,
# tambem usado por Intel): removê-lo quebraria a GPU integrada.
_AMD_SPECIFIC: list[str] = ["vulkan-radeon", "lib32-vulkan-radeon"]

_NVIDIA_INSTALL: dict[str, list[str]] = {
    "arch": ["nvidia-utils", "lib32-nvidia-utils", "opencl-nvidia", "nvidia-settings"],
    "fedora": ["akmod-nvidia", "xorg-x11-drv-nvidia-cuda"],
    "debian": ["nvidia-driver"],
}


class GpuStep(Step):
    id = "05"
    title = "Configurar GPU / drivers"
    description = (
        "Detecta o fabricante da GPU e instala os drivers certos (AMD: Vulkan RADV + VAAPI/VDPAU; "
        "NVIDIA: driver proprietario) e valida a sessao grafica, o OpenGL e o Vulkan. Em desktop de "
        "GPU unica ainda remove os residuos do fabricante ausente; em laptop/hibrido nunca remove "
        "driver. (Steam, Heroic e demais apps ficam no passo 10.)"
    )

    # Arquivos de sistema tocados na limpeza de residuos NVIDIA (somente Arch).
    _MKINITCPIO = Path("/etc/mkinitcpio.conf")
    _NVIDIA_MODPROBE = Path("/etc/modprobe.d/nvidia-drm.conf")
    _ENVIRONMENT = Path("/etc/environment")

    # O veredito vem dos probes de diagnostico (glxinfo/vulkaninfo/nvidia-smi),
    # nao da simples presenca dos pacotes.
    compliance_from_plan = False

    def tasks(self) -> list[StepTask]:
        vendors = hardware.gpu_vendors(self._list_gpus())
        distro = current_distro()
        present = sorted(vendors & {"amd", "nvidia"})
        absent = sorted({"amd", "nvidia"} - vendors)
        items: list[StepTask] = []

        for vendor in present:
            packages = self._vendor_packages(vendor, distro)
            listed = ", ".join(packages) if packages else "nenhum pacote mapeado para esta distro"
            extra = ""
            if vendor == "nvidia" and distro.is_arch:
                extra = " Tambem instala o modulo de kernel nvidia-open-dkms."
            items.append(
                StepTask(
                    key=f"driver-{vendor}",
                    label=f"Instalar os drivers da GPU {vendor.upper()}",
                    short_description=f"Drivers de video {vendor.upper()}",
                    description=f"Instala: {listed}.{extra}",
                    available=not distro.immutable,
                    unavailable_reason="sistema imutavel: os drivers vem na imagem base",
                    detect=partial(self._driver_ready, vendor, distro),
                    run=partial(self._install_vendor, vendor, distro),
                )
            )

        if self._should_cleanup_absent(vendors):
            for vendor in absent:
                items.append(
                    StepTask(
                        key=f"limpeza-{vendor}",
                        label=f"Remover residuos do driver {vendor.upper()}",
                        destructive=True,
                        description=(
                            f"Nao ha GPU {vendor.upper()} nesta maquina (desktop de GPU unica). Remove os pacotes "
                            "que sobraram do fabricante ausente. E destrutivo: pede confirmacao digitada."
                            + (
                                " Em NVIDIA/Arch tambem limpa o mkinitcpio, o modprobe.d e o /etc/environment."
                                if vendor == "nvidia"
                                else ""
                            )
                        ),
                        detect=partial(self._absent_vendor_clean, vendor),
                        run=partial(self._remove_absent_vendor, vendor, distro),
                    )
                )

        return items

    def apply(self) -> None:
        header(self, self.title, "Instala e valida os drivers da GPU")
        vendors = hardware.gpu_vendors(self._list_gpus())
        present = sorted(vendors & {"amd", "nvidia"})
        self.ctx.logger.write(
            f"Fabricante(s) de GPU detectado(s): {', '.join(present).upper() or 'nenhum dedicado (apenas integrada?)'}."
        )
        if not present:
            announce(
                self.ctx.logger,
                "warning",
                "Nenhuma GPU dedicada AMD/NVIDIA detectada; pulando instalacao de driver dedicado.",
            )
        if not self._should_cleanup_absent(vendors) and ({"amd", "nvidia"} - vendors):
            announce(
                self.ctx.logger,
                "info",
                "Laptop/hibrido detectado: mantendo os drivers do outro fabricante (nao removo nada automaticamente).",
            )
            self.add_hint(
                "Maquina hibrida (ou laptop): a limpeza automatica de driver so roda em desktop de GPU unica. "
                "Se tiver certeza de que quer remover o driver do fabricante ausente, faca manualmente."
            )
        super().apply()
        # A validacao roda sempre: e diagnostico, nao altera nada, e e ela que dita
        # o veredito da etapa (por isso compliance_from_plan = False).
        results = self._collect_gpu_results(vendors)
        self._render_gpu_summary(results)
        self._finalize(results, done=False)

    def _vendor_packages(self, vendor: str, distro) -> list[str]:
        table = _AMD_INSTALL if vendor == "amd" else _NVIDIA_INSTALL
        return list(table.get(distro.family, []))

    def _driver_ready(self, vendor: str, distro) -> bool:
        packages = self._vendor_packages(vendor, distro)
        if not packages:
            return False
        return all(system_installed(pkg) for pkg in packages)

    def _absent_vendor_clean(self, vendor: str) -> str | bool:
        targets = (
            [pkg for pkg in _AMD_SPECIFIC if system_installed(pkg)]
            if vendor == "amd"
            else installed_packages_matching("nvidia")
        )
        return "nada sobrou para remover" if not targets else False

    def _should_cleanup_absent(self, vendors: set[str]) -> bool:
        """A remocao do fabricante ausente so e segura em desktop de GPU unica.

        Em laptop (ou qualquer maquina com mais de um fabricante dedicado) nunca
        removemos driver: hibridos precisam dos dois e a troca de GPU e rara.
        """
        if hardware.is_laptop():
            return False
        return len(vendors & {"amd", "nvidia"}) == 1

    def status(self) -> None:
        vendors = hardware.gpu_vendors(self._list_gpus())
        results = self._collect_gpu_results(vendors)
        self._render_gpu_summary(results)
        self._finalize(results, done=False)

    def undo(self) -> None:
        self.ctx.logger.write(
            "Undo remove os pacotes Vulkan AMD adicionados pelo apply. NAO reinstala drivers de um "
            "fabricante removido nem reverte o initramfs (os backups .backup-pos-formatacao-* ficam em /etc)."
        )
        distro = current_distro()
        if distro.is_arch:
            targets = [pkg for pkg in _AMD_SPECIFIC if system_installed(pkg)]
            if targets:
                remove_system_packages(targets, self.ctx.runner)
            else:
                announce(self.ctx.logger, "skipped", "nenhum pacote Vulkan AMD para remover")
        else:
            announce(
                self.ctx.logger, "skipped", "undo automatico so cobre Arch; ajuste manualmente nas outras familias"
            )
        self.add_hint("Trocou de GPU? Reinstale o driver do hardware atual manualmente (ex.: rode este step de novo).")

    def _finalize(self, results: list[ProbeResult], *, done: bool) -> None:
        ok_count = sum(1 for item in results if item.status == "ok")
        warn_count = sum(1 for item in results if item.status == "warn")
        problem_count = sum(1 for item in results if item.status == "problem")
        if problem_count == 0:
            if done:
                self.mark_done(f"Concluido: {ok_count} OK e {warn_count} alerta(s).")
            if warn_count == 0:
                self.mark_applied("GPU e sessao grafica estao conforme esperado.")
            else:
                self.mark_attention(f"Concluido com {warn_count} alerta(s), sem falhas criticas.")
        else:
            if done:
                self.mark_done(f"Concluido com problemas: {problem_count} item(ns) exigem revisao.")
            self.mark_attention(f"Ha {problem_count} item(ns) que exigem revisao na validacao de GPU.")

    # -- instalacao / remocao de drivers -------------------------------------

    def _install_vendor(self, vendor: str, distro) -> None:
        announce(self.ctx.logger, "info", f"Instalando drivers para GPU {vendor.upper()}.")
        if distro.immutable:
            announce(
                self.ctx.logger,
                "warning",
                "Sistema imutavel: drivers nativos sao pulados (use a imagem base/Flatpak).",
            )
            return
        if vendor == "amd":
            for pkg in _AMD_INSTALL.get(distro.family, []):
                install_system_package(pkg, self.ctx.runner)
        elif vendor == "nvidia":
            self._install_nvidia(distro)

    def _install_nvidia(self, distro) -> None:
        if distro.is_fedora:
            ensure_rpmfusion(self.ctx.runner)
        for pkg in _NVIDIA_INSTALL.get(distro.family, []):
            install_system_package(pkg, self.ctx.runner)
        if distro.is_arch:
            # Modulo do kernel: a variante -open-dkms funciona em qualquer kernel.
            install_system_or_aur("nvidia-open-dkms", "nvidia-open-dkms", self.ctx.runner)
            self.add_hint(
                "No CachyOS o modulo NVIDIA ideal e o casado com o kernel "
                "(ex.: linux-cachyos-nvidia-open). Se preferir, instale-o no lugar do -dkms."
            )

    def _remove_absent_vendor(self, vendor: str, distro) -> None:
        if vendor == "amd":
            targets = [pkg for pkg in _AMD_SPECIFIC if system_installed(pkg)]
        else:  # nvidia: varre todos os residuos, nao so uma lista fixa.
            targets = installed_packages_matching("nvidia")
        if not targets:
            return
        announce(
            self.ctx.logger,
            "warning",
            f"GPU {vendor.upper()} nao esta presente, mas ha {len(targets)} pacote(s) residual(is): {', '.join(targets)}.",
        )
        phrase = f"REMOVER-{vendor.upper()}"
        if not self.ctx.runner.dry_run and not confirm_phrase(phrase, self.ctx.logger):
            self.add_hint(f"Remocao dos residuos {vendor.upper()} cancelada; rode de novo quando quiser limpar.")
            return
        remove_system_packages(targets, self.ctx.runner)
        if vendor == "nvidia" and distro.is_arch:
            self._clean_nvidia_system_files()

    def _clean_nvidia_system_files(self) -> None:
        self._clean_mkinitcpio_modules()
        self._remove_nvidia_modprobe()
        self._clean_environment_gl()
        self._regenerate_initramfs()

    def _clean_mkinitcpio_modules(self) -> None:
        try:
            text = self._MKINITCPIO.read_text(encoding="utf-8")
        except OSError:
            return
        match = re.search(r"^MODULES=\((.*?)\)", text, re.MULTILINE | re.DOTALL)
        if not match:
            return
        tokens = match.group(1).split()
        kept = [tok for tok in tokens if not tok.lower().startswith("nvidia")]
        if len(kept) == len(tokens):
            announce(self.ctx.logger, "skipped", "mkinitcpio.conf ja esta sem modulos NVIDIA")
            return
        new_line = "MODULES=(" + " ".join(kept) + ")"
        new_text = text[: match.start()] + new_line + text[match.end() :]
        backup_existing(self._MKINITCPIO, self.ctx.runner, sudo=True)
        write_text_sudo(self._MKINITCPIO, new_text, self.ctx.runner)

    def _remove_nvidia_modprobe(self) -> None:
        if not self._NVIDIA_MODPROBE.exists():
            return
        backup_existing(self._NVIDIA_MODPROBE, self.ctx.runner, sudo=True)
        self.ctx.runner.run(
            ["rm", "-f", str(self._NVIDIA_MODPROBE)],
            sudo=True,
            check=False,
            action="Removendo nvidia-drm.conf",
            show_progress=False,
        )

    def _clean_environment_gl(self) -> None:
        try:
            text = self._ENVIRONMENT.read_text(encoding="utf-8")
        except OSError:
            return
        lines = text.splitlines()
        kept = [ln for ln in lines if "__gl_" not in ln.lower() and "nvidia shader cache" not in ln.lower()]
        if len(kept) == len(lines):
            return
        backup_existing(self._ENVIRONMENT, self.ctx.runner, sudo=True)
        new_text = "\n".join(kept)
        if text.endswith("\n"):
            new_text += "\n"
        write_text_sudo(self._ENVIRONMENT, new_text, self.ctx.runner)

    def _regenerate_initramfs(self) -> None:
        if not command_exists("mkinitcpio"):
            self.add_hint("mkinitcpio nao encontrado; regenere o initramfs manualmente apos a limpeza.")
            return
        self.ctx.runner.run(
            ["mkinitcpio", "-P"],
            sudo=True,
            action="Regenerando initramfs",
            interactive=True,
            interactive_tty=True,
            manual_message="Comando interativo: o mkinitcpio pode pedir senha do sudo. Isso nao e travamento.",
        )

    # -- validacao ------------------------------------------------------------

    def _list_gpus(self) -> list[str]:
        if not command_exists("lspci"):
            return []
        result = self._run_probe(["lspci"], "Listando dispositivos PCI")
        if result.returncode != 0:
            return []
        return hardware.list_gpus(result.stdout)

    def _collect_gpu_results(self, vendors: set[str]) -> list[ProbeResult]:
        results = [self._probe_session_type(), self._probe_integrated_gl()]
        if "nvidia" in vendors:
            results.append(self._probe_prime_gl())
            results.append(self._probe_nvidia_smi())
        if "amd" in vendors:
            results.append(self._probe_amdgpu())
            results.append(self._probe_radv())
        return results

    def _probe_amdgpu(self) -> ProbeResult:
        if hardware.amdgpu_active():
            return ProbeResult("Driver AMD (amdgpu)", "ok", "modulo amdgpu carregado.")
        return ProbeResult(
            "Driver AMD (amdgpu)",
            "problem",
            "modulo amdgpu nao esta carregado.",
            "Verifique dmesg e se o firmware amdgpu (linux-firmware) esta instalado.",
        )

    def _probe_radv(self) -> ProbeResult:
        if not system_installed("vulkan-radeon"):
            return ProbeResult(
                "Vulkan AMD (RADV)",
                "problem",
                "pacote vulkan-radeon ausente.",
                "Instale vulkan-radeon (e lib32-vulkan-radeon) para Vulkan em jogos/Proton.",
            )
        if not command_exists("vulkaninfo"):
            return ProbeResult(
                "Vulkan AMD (RADV)",
                "warn",
                "vulkan-radeon instalado, mas vulkaninfo ausente para confirmar.",
                "Instale vulkan-tools para validar o Vulkan.",
            )
        result = self._run_probe(["vulkaninfo", "--summary"], "Consultando Vulkan (RADV)")
        output = self._combined_output(result).lower()
        if result.returncode == 0 and ("radv" in output or "amd radeon" in output):
            return ProbeResult("Vulkan AMD (RADV)", "ok", "driver Vulkan RADV ativo.")
        return ProbeResult(
            "Vulkan AMD (RADV)",
            "warn",
            "vulkaninfo nao confirmou o RADV.",
            self._truncate_probe_output(result),
        )

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
        return len(self._list_gpus())

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

    def _run_probe(self, cmd: list[str], action: str) -> subprocess.CompletedProcess[str]:
        # Leitura pura (roda mesmo em dry-run); nunca levanta.
        return capture(cmd, cwd=self.ctx.root)

    def _render_gpu_summary(self, results: list[ProbeResult]) -> None:
        header(self, self.title, "Diagnostico amigavel de sessao grafica e GPUs")
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
            announce(self.ctx.logger, "done", "Tudo certo com sessao grafica e GPUs avaliados.")
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
    # Catalogo de apps: ja instalado nao reinstala sozinho (so via Reinstalar).
    skip_if_installed = True
    description = (
        "Instala os apps principais: Steam e Heroic (jogos), comunicacao (Discord, ZapZap, TeamSpeak), "
        "utilitarios (Solaar, LocalSend, Flatseal, Bitwarden, Linux Toys), ONLYOFFICE, auto-cpufreq "
        "e o Codex CLI. Usa pacote nativo/AUR quando possivel, com fallback para Flatpak."
    )
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
        "Bitwarden": {
            "system_aliases": ("bitwarden",),
            "flatpak_id": "com.bitwarden.desktop",
            "appimage_paths": (),
            "desktop_paths": (),
            "kind": "flatpak",
        },
        "Linux Toys": {
            "system_aliases": ("linuxtoys",),
            "flatpak_id": None,
            "appimage_paths": (),
            "desktop_paths": (),
            "kind": "cli",
        },
        "Solaar": {
            "system_aliases": ("solaar",),
            "flatpak_id": "io.github.pwr_solaar.solaar",
            "appimage_paths": (),
            "desktop_paths": (),
            "kind": "flatpak",
        },
        "Flatseal": {
            "system_aliases": (),
            "flatpak_id": "com.github.tchx84.Flatseal",
            "appimage_paths": (),
            "desktop_paths": (),
            "kind": "flatpak",
        },
        "LocalSend": {
            "system_aliases": ("localsend", "localsend-bin"),
            "flatpak_id": "org.localsend.localsend_app",
            "appimage_paths": (),
            "desktop_paths": (),
            "kind": "flatpak",
        },
    }
    # Explicacao mostrada ao usuario ao lado de cada app na lista do Aplicar.
    app_help = {
        "Steam": "Loja e launcher de jogos da Valve. Em Fedora habilita o RPM Fusion antes; se nao houver pacote nativo, cai no Flatpak.",
        "Heroic": "Launcher para jogos da Epic Games, GOG e Amazon. Pacote nativo/AUR com fallback para Flatpak.",
        "Discord": "Chat de voz e texto. Instalado via Flatpak.",
        "TeamSpeak": "Chat de voz para jogos. Instalado via Flatpak.",
        "ZapZap": "Cliente de WhatsApp para desktop. Pacote nativo/AUR com fallback para Flatpak.",
        "ONLYOFFICE": "Suite de escritorio compativel com Word/Excel/PowerPoint. Instalado via Flatpak.",
        "Google Chrome": "Navegador do Google. Instalado via Flatpak.",
        "Minecraft Bedrock Launcher": "Roda a versao Bedrock do Minecraft no Linux. Instalado via Flatpak.",
        "Codex CLI": "Assistente de codigo da OpenAI no terminal. Instala nodejs + npm e o pacote global @openai/codex.",
        "auto-cpufreq": "Gerencia a frequencia da CPU automaticamente (economiza bateria no notebook). Instala o pacote e habilita o daemon; sem pacote nativo, usa o instalador oficial do GitHub.",
        "Bitwarden": "Gerenciador de senhas. Instalado via Flatpak.",
        "Linux Toys": "Colecao de utilitarios e tweaks. Instalado pelo script oficial (curl | bash).",
        "Solaar": "Gerenciador de dispositivos Logitech (Unifying). Prioriza o Flatpak, com fallback para pacote nativo/AUR; se nao houver nenhum, instala o Piper como alternativa.",
        "Flatseal": "Editor grafico de permissoes dos apps Flatpak. Instalado via Flatpak.",
        "LocalSend": "Envia arquivos entre dispositivos na mesma rede (tipo AirDrop). Prioriza o Flatpak, com fallback para pacote nativo/AUR.",
    }

    # Uma linha curta (para o card estilo Flathub) + categoria (colore o avatar).
    app_short = {
        "Steam": "Loja e launcher de jogos da Valve",
        "Heroic": "Jogos da Epic, GOG e Amazon",
        "Discord": "Chat de voz e texto",
        "TeamSpeak": "Chat de voz para jogos",
        "ZapZap": "WhatsApp para desktop",
        "ONLYOFFICE": "Suite de escritorio (Word/Excel/PPT)",
        "Google Chrome": "Navegador do Google",
        "Minecraft Bedrock Launcher": "Minecraft Bedrock no Linux",
        "Codex CLI": "Assistente de codigo da OpenAI no terminal",
        "auto-cpufreq": "Economia de bateria (frequencia da CPU)",
        "Bitwarden": "Gerenciador de senhas",
        "Linux Toys": "Colecao de utilitarios e tweaks",
        "Solaar": "Dispositivos Logitech (Unifying)",
        "Flatseal": "Editor de permissoes de apps Flatpak",
        "LocalSend": "Envia arquivos na rede local",
    }
    app_category = {
        "Steam": "jogos",
        "Heroic": "jogos",
        "Discord": "comunicacao",
        "TeamSpeak": "comunicacao",
        "ZapZap": "comunicacao",
        "ONLYOFFICE": "escritorio",
        "Google Chrome": "navegador",
        "Minecraft Bedrock Launcher": "jogos",
        "Codex CLI": "dev",
        "auto-cpufreq": "sistema",
        "Bitwarden": "utilitarios",
        "Linux Toys": "utilitarios",
        "Solaar": "utilitarios",
        "Flatseal": "utilitarios",
        "LocalSend": "utilitarios",
    }

    # Icone (id Flathub) SO para exibicao, para apps sem flatpak_id (instalados por
    # pacote/script). Nao muda a instalacao — so alimenta o card com um icone real.
    app_icon_id = {
        "Steam": "com.valvesoftware.Steam",
        "auto-cpufreq": "",
        "Codex CLI": "",
        "Linux Toys": "",
    }
    # Binarios que o app coloca no PATH. A deteccao checa command_exists nesses de
    # forma robusta para TODOS os apps (nao so casos especiais): cobre instalacoes
    # cujo nome de pacote difere do binario, instalacoes por script e ambientes
    # onde a query de pacote nao resolve. Apps so-Flatpak nao expoem binario no
    # PATH (rodam via `flatpak run`) e ja sao detectados pelo flatpak_id.
    app_commands = {
        "Steam": ("steam",),
        "Heroic": ("heroic",),
        "Discord": ("discord",),
        "TeamSpeak": ("teamspeak3", "ts3client"),
        "ZapZap": ("zapzap",),
        "ONLYOFFICE": ("onlyoffice-desktopeditors", "desktopeditors"),
        "Google Chrome": ("google-chrome-stable", "google-chrome"),
        "Minecraft Bedrock Launcher": ("mcpelauncher-client", "mcpelauncher-ui-qt"),
        "Codex CLI": ("codex",),
        "auto-cpufreq": ("auto-cpufreq",),
        "Bitwarden": ("bitwarden",),
        "Linux Toys": ("linuxtoys",),
        "Solaar": ("solaar",),
        "LocalSend": ("localsend",),
    }

    def _icon_for(self, name: str) -> str:
        flatpak_id = self.apps[name].get("flatpak_id")
        if flatpak_id:
            return str(flatpak_id)
        return self.app_icon_id.get(name, "")

    def tasks(self) -> list[StepTask]:
        return [
            StepTask(
                key=name,
                label=name,
                description=self.app_help.get(name, ""),
                short_description=self.app_short.get(name, ""),
                icon=self._icon_for(name),
                category=self.app_category.get(name, ""),
                detect=partial(self._detect_source_label, name),
                run=partial(self._install_app, name),
                remove=partial(self._remove_app, name),
                detail="nao instalado",
            )
            for name in self.apps
        ]

    def _detect_source_label(self, name: str) -> str | bool:
        source = self._detect_install_source(name)
        return f"instalado via {source}" if source else False

    def _remove_app(self, name: str) -> None:
        """Remove o app pela origem detectada: Flatpak e/ou pacote nativo/AUR.

        Nao mexe em runtimes compartilhados (ex.: nodejs/npm do Codex CLI) nem em
        coisas instaladas por script sem pacote — para essas, avisa remocao manual.
        """
        definition = self.apps[name]
        header(self, f"Remover {name}")
        removed = False
        flatpak_id = definition["flatpak_id"]
        if flatpak_id and flatpak_installed(str(flatpak_id)):
            remove_flatpak(str(flatpak_id), self.ctx.runner)
            removed = True
        # Codex CLI compartilha nodejs/npm com o sistema: so remove o pacote global.
        if name == "Codex CLI":
            if npm_global_installed("@openai/codex"):
                self.ctx.runner.run(
                    ["npm", "uninstall", "-g", "@openai/codex"],
                    sudo=True,
                    check=False,
                    action="Removendo Codex CLI global",
                )
                removed = True
        else:
            installed = [alias for alias in definition["system_aliases"] if system_installed(alias)]
            if installed:
                remove_system_packages(installed, self.ctx.runner)
                removed = True
        if not removed:
            self.ctx.logger.write(
                f"{badge('aviso', Color.WARNING)} {name}: remocao automatica indisponivel "
                "(instalado por script?). Remova manualmente se necessario."
            )
            self.add_hint(f"{name} pode precisar de remocao manual.")

    def apply(self) -> None:
        header(self, self.title, "Instalando apps principais e Codex CLI")
        super().apply()

    def status(self) -> None:
        header(self, self.title, "Verificando origem detectada de cada app")
        super().status()

    def _install_app(self, name: str) -> None:
        definition = self.apps[name]
        if name == "Steam":
            self._install_steam()
        elif name == "Heroic":
            self._install_system_or_flatpak(
                "heroic-games-launcher", "heroic-games-launcher-bin", "com.heroicgameslauncher.hgl"
            )
        elif name == "ZapZap":
            self._install_system_or_flatpak("zapzap", "zapzap", "com.rtosta.zapzap")
        elif name == "LocalSend":
            install_flatpak_or_system("org.localsend.localsend_app", "localsend", "localsend-bin", self.ctx.runner)
        elif name == "Solaar":
            self._install_solaar()
        elif name == "Linux Toys":
            self._install_linuxtoys()
        elif name == "Codex CLI":
            self._install_codex()
        elif name == "auto-cpufreq":
            self._install_auto_cpufreq()
        elif definition["kind"] == "flatpak":
            header(self, f"{name} - Flatpak")
            install_flatpak(str(definition["flatpak_id"]), self.ctx.runner)
        else:
            raise RuntimeError(f"sem instalador definido para {name}")

    def _install_codex(self) -> None:
        header(self, "Codex CLI")
        install_system_package("nodejs", self.ctx.runner)
        install_system_package("npm", self.ctx.runner)
        if npm_global_installed("@openai/codex"):
            self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} @openai/codex ja instalado globalmente")
            return
        self.ctx.runner.run(
            ["npm", "install", "-g", "@openai/codex"], sudo=True, action="Instalando Codex CLI globalmente"
        )

    def _install_linuxtoys(self) -> None:
        if command_exists("linuxtoys"):
            self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} Linux Toys ja instalado (linuxtoys no PATH).")
            return
        header(self, "Linux Toys", "Instalando pelo script oficial (colecao de utilitarios e tweaks)")
        build_dir = Path("/tmp/linuxtoys")
        if build_dir.exists():
            self.ctx.logger.write(
                f"{badge('aviso', Color.WARNING)} limpando build anterior em {build_dir} para evitar falha de makepkg"
            )
            self.ctx.runner.run(
                ["rm", "-rf", str(build_dir)],
                check=False,
                action="Limpando build temporario anterior do Linux Toys",
                show_progress=False,
            )
        self.ctx.runner.run(
            "curl -fsSL https://linux.toys/install.sh | bash",
            shell=True,
            action="Baixando e executando instalador do Linux Toys",
        )

    def _install_auto_cpufreq(self) -> None:
        source = self._detect_install_source("auto-cpufreq")
        if source:
            self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} auto-cpufreq ja detectado via {source}")
            self._enable_auto_cpufreq_daemon()
            return
        header(self, "auto-cpufreq", "Instalando gerenciador automatico de CPU")
        if install_system_or_aur("auto-cpufreq", "auto-cpufreq", self.ctx.runner):
            self._enable_auto_cpufreq_daemon()
            return
        if current_distro().immutable:
            self.ctx.logger.write(
                f"{badge('aviso', Color.WARNING)} sistema imutavel: o instalador do auto-cpufreq escreve em /usr "
                "(somente leitura). Pulando instalacao via GitHub."
            )
            self.add_hint("auto-cpufreq nao foi instalado: sistema imutavel sem pacote nativo disponivel.")
            return
        self.ctx.logger.write(
            f"{badge('aviso', Color.WARNING)} auto-cpufreq nao possui Flatpak oficial; usando instalador do GitHub."
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
                f"{badge('aviso', Color.WARNING)} binario auto-cpufreq nao encontrado para habilitar o daemon."
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
                f"{badge('aviso', Color.WARNING)} Steam nao apareceu nos repositorios apt atuais. "
                "Habilite multiverse/non-free/non-free-firmware conforme sua distro e rode esta etapa novamente."
            )
            self.mark_manual("Steam depende de repositorios adicionais no Debian/Ubuntu.")
            return
        # Fedora sem RPM Fusion, imutaveis ou repos sem Steam: caimos no Flatpak.
        install_flatpak("com.valvesoftware.Steam", self.ctx.runner)

    def _install_solaar(self) -> None:
        header(self, "Solaar", "Gerenciador de dispositivos Logitech")
        if install_flatpak_or_system("io.github.pwr_solaar.solaar", "solaar", "solaar", self.ctx.runner):
            return
        self.ctx.logger.write(
            f"{badge('aviso', Color.WARNING)} Solaar indisponivel nos repositorios; instalando Piper como alternativa."
        )
        header(self, "Piper", "Alternativa ao Solaar para mouses gaming")
        if install_system_or_aur("piper", "piper", self.ctx.runner):
            return
        install_flatpak("org.freedesktop.Piper", self.ctx.runner)

    def _install_system_or_flatpak(self, system_pkg: str, aur_pkg: str | None, flatpak_id: str) -> None:
        install_system_or_flatpak(system_pkg, aur_pkg, flatpak_id, self.ctx.runner)

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
        # Robusto e uniforme para todos os apps: binario no PATH conta como instalado
        # (pacote com nome diferente, instalacao por script, ou query de pacote que
        # nao resolve). Codex ainda pode estar so como pacote global do npm.
        for command in self.app_commands.get(app_name, ()):
            if command_exists(command):
                return f"cli ({command} no PATH)"
        if app_name == "Codex CLI" and npm_global_installed("@openai/codex"):
            return "npm global"
        return None


class SunshineStep(InputGroupMixin, Step):
    id = "13"
    title = "Sunshine / Moonlight"
    description = (
        "Instala e configura o Sunshine (game streaming p/ Moonlight): permissoes (grupo input/udev), "
        "autostart no KDE, regras de firewall (UFW) e um launcher no menu."
    )
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

    def tasks(self) -> list[StepTask]:
        return [
            StepTask(
                key="pacote",
                label="Instalar o Sunshine",
                short_description="Servidor de streaming para o Moonlight",
                description=(
                    "Instala o pacote sunshine, o servidor de streaming de jogos que o Moonlight "
                    "acessa a partir de outro PC, TV ou celular."
                ),
                detect=lambda: system_installed("sunshine") or command_exists("sunshine"),
                run=lambda: install_system_package("sunshine", self.ctx.runner),
            ),
            StepTask(
                key="grupo-input",
                label="Adicionar seu usuario ao grupo 'input'",
                short_description="Permite emular teclado/mouse/controle",
                description=(
                    "Sem isso o Sunshine nao consegue emular teclado/mouse/controle do cliente remoto. "
                    "Exige logout/login para valer."
                ),
                detect=lambda: self._user_in_group("input"),
                run=self._ensure_input_group_task,
            ),
            StepTask(
                key="udev",
                label="Criar a regra udev do /dev/uinput",
                short_description="Regra udev de acesso ao uinput",
                description=(
                    f"Escreve {self.udev_rule_file} dando ao grupo input acesso ao uinput (necessario para "
                    "o controle remoto funcionar) e recarrega as regras."
                ),
                detect=self._udev_rule_ready,
                run=self._write_udev_rule,
            ),
            StepTask(
                key="autostart",
                label="Iniciar o Sunshine junto com o KDE",
                short_description="Sobe o Sunshine no login do KDE",
                description=f"Cria {self.autostart_file} para o Sunshine subir sozinho no login.",
                detect=self._autostart_ready,
                run=self._write_autostart,
            ),
            StepTask(
                key="launcher",
                label="Criar o atalho no menu de aplicativos",
                short_description="Atalho .desktop do Sunshine",
                description="Cria um .desktop do Sunshine caso o pacote do sistema nao traga um.",
                detect=lambda: self._find_existing_launcher() is not None,
                run=self._ensure_menu_launcher,
            ),
            StepTask(
                key="ufw",
                label="Liberar as portas do Sunshine no firewall",
                short_description="Abre as portas do Moonlight no UFW",
                description=(
                    "Libera no UFW as portas 47984-47990/tcp, 48010/tcp e 47998-48000/udp usadas pelo "
                    "Moonlight. Se o UFW nao estiver ativo, nada e feito."
                ),
                detect=self._ufw_rules_ready,
                run=self._configure_ufw,
            ),
            StepTask(
                key="iniciar",
                label="Iniciar o Sunshine agora",
                short_description="Sobe o Sunshine (web em :47990)",
                description="Sobe o Sunshine em segundo plano; a interface web fica em https://localhost:47990.",
                detect=self._sunshine_running,
                run=self._start_sunshine,
            ),
        ]

    def apply(self) -> None:
        header(self, self.title, "Instalando Sunshine e integrando com KDE/Moonlight")
        super().apply()

    def _autostart_ready(self) -> bool:
        return (
            self.autostart_file.exists()
            and self.autostart_file.read_text(encoding="utf-8", errors="ignore") == self._autostart_content()
        )

    def _ensure_input_group_task(self) -> None:
        if not self._ensure_input_group():
            self.mark_manual(f"Execute logout/login ou reinicie para o grupo input valer para {self.ctx.user.name}.")

    def _write_udev_rule(self) -> None:
        if self._udev_rule_ready():
            self.ctx.logger.write(
                f"{badge('ok', Color.SUCCESS)} regra udev do Sunshine ja esta configurada: {self.udev_rule_file}"
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
                f"{badge('aviso', Color.WARNING)} existe sunshine.service de usuario; revise se houver Sunshine duplicado: {self.user_service_file}"
            )

    def _ensure_menu_launcher(self) -> None:
        existing = self._find_existing_launcher()
        if existing and existing != self.fallback_desktop_file:
            self.ctx.logger.write(
                f"{badge('ok', Color.SUCCESS)} lancador Sunshine ja fornecido pelo sistema: {existing}"
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
            self.ctx.logger.write(f"{badge('aviso', Color.WARNING)} UFW nao instalado. Pulando regras de firewall.")
            return
        if not self._ufw_active():
            self.ctx.logger.write(
                f"{badge('aviso', Color.WARNING)} UFW existe, mas nao esta ativo. Pulando regras de firewall."
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
        # capture (nao Runner): a sondagem do card roda em dry-run e o Runner
        # devolveria None; a leitura do pgrep tem de acontecer de verdade.
        return capture(["pgrep", "-u", self.ctx.user.name, "-x", "sunshine"]).returncode == 0

    def _start_sunshine(self) -> None:
        if not command_exists("sunshine") and not Path("/usr/bin/sunshine").exists() and not self.ctx.runner.dry_run:
            self.ctx.logger.write(
                f"{badge('aviso', Color.WARNING)} binario sunshine nao encontrado para iniciar agora."
            )
            return
        if self._sunshine_running():
            self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} Sunshine ja esta rodando")
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
        # Leitura via capture (roda em dry-run). `ufw status` normalmente pede root;
        # sem privilegio nao ha saida e degradamos para "ok" (nao incomodar). Quando
        # roda com privilegio (ex.: reforja ja sob sudo), valida as regras de fato.
        output = capture(["ufw", "status"]).stdout or ""
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
            self.ctx.logger.write(f"{badge('dry-run', Color.DRY_RUN)} removeria {path}")
        else:
            path.unlink(missing_ok=True)

    def _remove_fallback_desktop_if_managed(self) -> None:
        if not self.fallback_desktop_file.exists():
            if self.ctx.runner.dry_run:
                self.ctx.logger.write(
                    f"{badge('dry-run', Color.DRY_RUN)} removeria fallback desktop se existisse: {self.fallback_desktop_file}"
                )
            return
        if self._desktop_launches_sunshine(self.fallback_desktop_file):
            self._remove_user_file(self.fallback_desktop_file)
        else:
            self.ctx.logger.write(
                f"{badge('aviso', Color.WARNING)} fallback desktop parece customizado; preservando: {self.fallback_desktop_file}"
            )

    def _remove_udev_rule_if_managed(self) -> None:
        if self.ctx.runner.dry_run:
            self.ctx.logger.write(
                f"{badge('dry-run', Color.DRY_RUN)} removeria {self.udev_rule_file} se contiver a regra gerenciada"
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
                f"{badge('aviso', Color.WARNING)} regra udev parece customizada; preservando: {self.udev_rule_file}"
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
