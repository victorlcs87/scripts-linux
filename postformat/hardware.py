from __future__ import annotations

import os
import re
import socket
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .core import Color, UserInfo, badge, command_exists, write_text
from .platform import current_distro
from .steps_base import StepContext

# Tipos de chassi do DMI que indicam notebook/portatil (SMBIOS spec).
_LAPTOP_CHASSIS = {8, 9, 10, 11, 14, 30, 31, 32}


def report_path(user: UserInfo) -> Path:
    """Caminho estavel do inventario, consumivel por outras etapas."""
    return user.home / ".cache/scripts-linux/hardware/hardware-info.txt"


@dataclass
class HardwareFacts:
    cpu_model: str = ""
    ram_total: str = ""
    gpus: list[str] = field(default_factory=list)
    has_nvidia: bool = False
    nvidia_gpu_name: str | None = None
    nvidia_driver_version: str = ""
    has_touchpad: bool = False
    is_laptop: bool = False
    hostname: str = ""
    os_pretty: str = ""
    kernel: str = ""
    disks: list[str] = field(default_factory=list)
    desktop: str = ""


def _capture(cmd: list[str]) -> str:
    """Executa um comando somente-leitura e devolve a saida (vazia se faltar)."""
    if not command_exists(cmd[0]):
        return ""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except (FileNotFoundError, OSError):
        return ""
    return result.stdout or ""


def has_touchpad() -> bool:
    """Detecta touchpad lendo /proc/bus/input/devices (usado pela etapa de gestos)."""
    devices = Path("/proc/bus/input/devices")
    try:
        text = devices.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return True
    return "touchpad" in text.lower()


def is_laptop() -> bool:
    chassis = Path("/sys/class/dmi/id/chassis_type")
    try:
        value = int(chassis.read_text(encoding="utf-8", errors="ignore").strip())
    except (OSError, ValueError):
        return False
    return value in _LAPTOP_CHASSIS


def list_gpus(output: str | None = None) -> list[str]:
    """Lista GPUs a partir do `lspci` (VGA/3D/Display)."""
    text = output if output is not None else _capture(["lspci"])
    gpus: list[str] = []
    for line in text.splitlines():
        if re.search(r"vga compatible controller|3d controller|display controller", line, re.IGNORECASE):
            description = line.split(":", 2)[-1].strip() if ":" in line else line.strip()
            gpus.append(description or line.strip())
    return gpus


def nvidia_gpu_name(output: str | None = None) -> str | None:
    """Extrai o nome da GPU NVIDIA (ou None se ausente)."""
    if output is None:
        out = _capture(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])
        if out.strip():
            return out.strip().splitlines()[0].strip()
    text = output or ""
    for raw_line in text.splitlines():
        if re.search(r"GeForce|Quadro|Tesla|RTX|GTX|MX\d", raw_line):
            cleaned = " ".join(raw_line.strip("| ").split())
            if cleaned:
                return cleaned
    return None


def _nvidia_driver_version() -> str:
    try:
        v = Path("/sys/module/nvidia/version").read_text(encoding="utf-8").strip()
        if v:
            return v
    except OSError:
        pass
    out = _capture(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"])
    return out.strip().splitlines()[0].strip() if out.strip() else ""


def _cpu_model() -> str:
    # /proc/cpuinfo nao depende do locale (lscpu pode estar traduzido).
    try:
        text = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        text = ""
    for raw_line in text.splitlines():
        if raw_line.lower().startswith("model name"):
            return raw_line.split(":", 1)[1].strip()
    return ""


def _ram_total() -> str:
    try:
        text = Path("/proc/meminfo").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    match = re.search(r"^MemTotal:\s+(\d+)\s*kB", text, re.MULTILINE)
    if not match:
        return ""
    gib = int(match.group(1)) / (1024 * 1024)
    return f"{gib:.1f} GiB"


def _hostname() -> str:
    try:
        return Path("/etc/hostname").read_text(encoding="utf-8").strip()
    except OSError:
        return socket.gethostname()


def _os_pretty() -> str:
    try:
        text = Path("/etc/os-release").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    for line in text.splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def _kernel_version() -> str:
    return _capture(["uname", "-r"]).strip()


def _list_disks() -> list[str]:
    out = _capture(["lsblk", "-d", "-o", "NAME,SIZE", "--noheadings"])
    disks = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and not re.match(r"^(loop|zram)", parts[0]):
            disks.append(f"{parts[0]}  {parts[1]}")
    return disks


def _desktop_env() -> str:
    return os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("DESKTOP_SESSION", "")


def read_facts() -> HardwareFacts:
    """Fonte unica de fatos de hardware para qualquer etapa que precise."""
    gpus = list_gpus()
    nvidia_name = nvidia_gpu_name()
    has_nvidia = bool(nvidia_name) or any("nvidia" in gpu.lower() for gpu in gpus)
    return HardwareFacts(
        cpu_model=_cpu_model(),
        ram_total=_ram_total(),
        gpus=gpus,
        has_nvidia=has_nvidia,
        nvidia_gpu_name=nvidia_name,
        nvidia_driver_version=_nvidia_driver_version() if has_nvidia else "",
        has_touchpad=has_touchpad(),
        is_laptop=is_laptop(),
        hostname=_hostname(),
        os_pretty=_os_pretty(),
        kernel=_kernel_version(),
        disks=_list_disks(),
        desktop=_desktop_env(),
    )


def facts_summary(facts: HardwareFacts) -> list[str]:
    """Resumo legivel dos fatos, usado no relatorio e no status da etapa."""
    lines = [
        f"CPU: {facts.cpu_model or 'desconhecida'}",
        f"RAM total: {facts.ram_total or 'desconhecida'}",
        f"GPUs: {', '.join(facts.gpus) if facts.gpus else 'nenhuma detectada'}",
        f"NVIDIA: {facts.nvidia_gpu_name if facts.has_nvidia else 'nao detectada'}",
        f"Touchpad: {'sim' if facts.has_touchpad else 'nao'}",
        f"Tipo: {'notebook' if facts.is_laptop else 'desktop'}",
    ]
    return lines


def render_summary(facts: HardwareFacts) -> list[str]:
    """Painel visual estilo fastfetch com os dados coletados."""
    rows: list[tuple[str, str, str]] = [
        ("host",   facts.hostname  or "desconhecido", Color.INFO),
        ("os",     facts.os_pretty or "desconhecido", Color.INFO),
        ("kernel", facts.kernel    or "desconhecido", Color.INFO),
        ("tipo",   "Notebook" if facts.is_laptop else "Desktop", Color.ACCENT),
        ("cpu",    facts.cpu_model or "desconhecida",  Color.ACCENT),
        ("ram",    facts.ram_total or "desconhecida",  Color.ACCENT),
    ]
    for gpu in (facts.gpus or []):
        is_nvidia = "nvidia" in gpu.lower()
        display = (facts.nvidia_gpu_name or gpu) if is_nvidia else gpu
        tone = Color.WARNING if is_nvidia else Color.SUCCESS
        rows.append(("gpu", display, tone))
        if is_nvidia and facts.nvidia_driver_version:
            rows.append(("driver", facts.nvidia_driver_version, Color.WARNING))
    for disk in (facts.disks or []):
        rows.append(("disco", disk, Color.MUTED))
    if facts.desktop:
        rows.append(("de", facts.desktop, Color.MUTED))

    W = 55  # largura interna visivel (sem os │)
    title = "  Resumo de Hardware"
    sep = f"{Color.BOX}├{'─' * W}┤{Color.RESET}"

    out: list[str] = []
    out.append(f"{Color.BOX}╭{'─' * W}╮{Color.RESET}")
    out.append(f"{Color.BOX}│{Color.BOLD}{Color.TITLE}{title}{' ' * (W - len(title))}│{Color.RESET}")
    out.append(sep)
    for label, value, tone in rows:
        lbl_plain = f"[{label}]"
        lbl_colored = badge(label, tone)
        prefix_visible = 2 + len(lbl_plain) + 2   # "  [label]  "
        max_val = W - prefix_visible - 1
        val = value[:max_val] if len(value) > max_val else value
        padding = W - prefix_visible - len(val)
        out.append(f"{Color.BOX}│{Color.RESET}  {lbl_colored}  {val}{' ' * padding}{Color.BOX}│{Color.RESET}")
    out.append(f"{Color.BOX}╰{'─' * W}╯{Color.RESET}")
    return out


def _missing_tool_hint(tool: str) -> str:
    distro = current_distro()
    if tool == "inxi":
        cmd = "sudo pacman -S inxi" if distro.is_arch else "sudo apt-get install inxi"
        return f"(inxi nao instalado — para um resumo mais completo: {cmd})"
    if tool == "dmidecode":
        cmd = "sudo pacman -S dmidecode" if distro.is_arch else "sudo apt-get install dmidecode"
        return f"(dmidecode nao instalado — instale para detalhar RAM/placa-mae/BIOS: {cmd})"
    return f"(comando '{tool}' nao encontrado — pulei essa parte)"


def collect_report(ctx: StepContext) -> Path:
    """Coleta o inventario de hardware e grava num arquivo estavel.

    Usa o Runner (dry-run aware, sudo para dmidecode) para executar cada comando e
    captura a saida para montar o relatorio com write_text.
    """
    runner = ctx.runner
    parts: list[str] = []

    def run_capture(cmd: list[str], *, sudo: bool = False, timeout: int = 60) -> str:
        tool = cmd[0]
        if not command_exists(tool):
            return _missing_tool_hint(tool)
        # Envolve em `timeout` para que uma ferramenta lenta/travada (ex.: inxi
        # sondando discos/rede sem TTY) nunca congele a coleta inteira.
        full_cmd = ["timeout", "-k", "5", str(timeout), *cmd] if command_exists("timeout") else cmd
        result = runner.run(
            full_cmd,
            sudo=sudo,
            check=False,
            show_progress=False,
            quiet_success=True,
            action=f"Coletando {tool}",
        )
        if result is None:  # dry-run
            return "(dry-run: comando nao executado)"
        if result.returncode == 124:
            return f"(comando '{tool}' excedeu {timeout}s e foi interrompido)"
        return (result.stdout or "").rstrip() or f"(sem saida; retorno {result.returncode})"

    def section(title: str) -> None:
        parts.append("")
        parts.append("=" * 40)
        parts.append(f"## {title}")
        parts.append("=" * 40)

    facts = read_facts()
    parts.append(f"RELATORIO DE HARDWARE - {datetime.now():%Y-%m-%d %H:%M:%S}")
    parts.append(f"Host: {_capture(['hostname']).strip() or 'desconhecido'}")
    parts.append("")
    parts.append("--- Resumo ---")
    parts.extend(facts_summary(facts))

    section("Sistema Operacional e Kernel")
    try:
        parts.append(Path("/etc/os-release").read_text(encoding="utf-8", errors="ignore").rstrip())
    except OSError:
        parts.append("(nao consegui ler /etc/os-release)")
    parts.append("")
    parts.append("--- uname -a ---")
    parts.append(run_capture(["uname", "-a"]))

    section("CPU")
    parts.append(run_capture(["lscpu"]))

    section("Memoria RAM")
    parts.append(run_capture(["free", "-h"]))
    parts.append("")
    parts.append("--- Pentes de RAM (dmidecode) ---")
    parts.append(run_capture(["dmidecode", "--type", "memory"], sudo=True))

    section("GPU(s)")
    parts.append(run_capture(["lspci", "-k"]))
    if command_exists("nvidia-smi"):
        parts.append("")
        parts.append("--- nvidia-smi ---")
        parts.append(run_capture(["nvidia-smi"]))

    section("Armazenamento (discos e particoes)")
    parts.append(run_capture(["lsblk", "-f"]))
    parts.append("")
    parts.append("--- Uso de disco (df -h) ---")
    parts.append(run_capture(["df", "-h"]))

    section("Placa-mae e BIOS")
    parts.append(run_capture(["dmidecode", "--type", "baseboard"], sudo=True))
    parts.append("")
    parts.append(run_capture(["dmidecode", "--type", "bios"], sudo=True))

    section("Dispositivos PCI")
    parts.append(run_capture(["lspci"]))

    section("Dispositivos USB")
    parts.append(run_capture(["lsusb"]))

    section("Resumo inxi")
    parts.append(run_capture(["inxi", "-Fxxxz", "-c0", "-y", "80"], timeout=30))

    parts.append("")
    parts.append(f"Concluido em: {datetime.now():%Y-%m-%d %H:%M:%S}")
    parts.append("")

    destino = report_path(ctx.user)
    write_text(destino, "\n".join(parts), runner)
    return destino


def read_saved_report(user: UserInfo) -> str | None:
    """Le o ultimo inventario salvo, se existir (para outras etapas consultarem)."""
    path = report_path(user)
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
