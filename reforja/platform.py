from __future__ import annotations

import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .core import Color, Runner, announce, badge, command_exists, write_text_sudo


class UnsupportedDistroError(RuntimeError):
    pass


@dataclass(frozen=True)
class Distro:
    id: str
    id_like: tuple[str, ...]
    family: str
    immutable: bool = False

    @property
    def is_arch(self) -> bool:
        return self.family == "arch"

    @property
    def is_debian(self) -> bool:
        return self.family == "debian"

    @property
    def is_fedora(self) -> bool:
        return self.family == "fedora"


def read_os_release(path: Path = Path("/etc/os-release")) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        raise UnsupportedDistroError("/etc/os-release nao encontrado; nao consegui detectar a distribuicao.")
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _detect_immutable(distro_id: str) -> bool:
    # Fedora Atomic / Bazzite e qualquer base ostree expoem este marcador em runtime.
    if Path("/run/ostree-booted").exists():
        return True
    # SteamOS usa root read-only controlado por steamos-readonly.
    if distro_id == "steamos" or shutil.which("steamos-readonly") is not None:
        status = subprocess.run(
            ["steamos-readonly", "status"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        if status.returncode == 0 and "enabled" in status.stdout.lower():
            return True
        # Sem conseguir checar o status, tratamos SteamOS como imutavel por seguranca.
        return distro_id == "steamos"
    return False


def detect_distro(path: Path = Path("/etc/os-release")) -> Distro:
    values = read_os_release(path)
    distro_id = values.get("ID", "").strip().lower()
    id_like = tuple(item.strip().lower() for item in values.get("ID_LIKE", "").split() if item.strip())
    candidates = {distro_id, *id_like}
    immutable = _detect_immutable(distro_id)
    if {"arch", "cachyos", "manjaro", "steamos"} & candidates:
        return Distro(id=distro_id, id_like=id_like, family="arch", immutable=immutable)
    if {"fedora", "rhel", "centos", "rocky", "almalinux", "bazzite", "nobara"} & candidates:
        return Distro(id=distro_id, id_like=id_like, family="fedora", immutable=immutable)
    if {"debian", "ubuntu", "linuxmint", "pop"} & candidates:
        return Distro(id=distro_id, id_like=id_like, family="debian", immutable=immutable)
    pretty = values.get("PRETTY_NAME") or distro_id or "desconhecida"
    raise UnsupportedDistroError(
        f"distribuicao nao suportada: {pretty}. Suportadas: Arch/CachyOS/SteamOS, Debian/Ubuntu e Fedora/Bazzite."
    )


def current_distro() -> Distro:
    return detect_distro()


def _quiet(cmd: list[str]) -> bool:
    return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode == 0


def system_installed(pkg: str) -> bool:
    distro = current_distro()
    if distro.is_arch:
        return shutil.which("pacman") is not None and _quiet(["pacman", "-Q", pkg])
    if distro.is_fedora:
        return shutil.which("rpm") is not None and _quiet(["rpm", "-q", pkg])
    if distro.is_debian:
        if shutil.which("dpkg-query") is None:
            return False
        result = subprocess.run(
            ["dpkg-query", "-W", "-f=${Status}", pkg],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        return result.returncode == 0 and "install ok installed" in result.stdout
    return False


def system_package_exists(pkg: str) -> bool:
    distro = current_distro()
    if distro.is_arch:
        return shutil.which("pacman") is not None and _quiet(["pacman", "-Si", pkg])
    if distro.is_fedora:
        # dnf pode estar ausente em bases atomic; nesse caso degradamos para False.
        return shutil.which("dnf") is not None and _quiet(["dnf", "list", "--available", pkg])
    if distro.is_debian:
        return shutil.which("apt-cache") is not None and _quiet(["apt-cache", "show", pkg])
    return False


def aur_helper() -> str | None:
    distro = current_distro()
    if not distro.is_arch or distro.immutable:
        return None
    for candidate in ("paru", "yay"):
        if command_exists(candidate):
            return candidate
    return None


def install_system_package(pkg: str, runner: Runner) -> None:
    if system_installed(pkg):
        announce(runner.logger, "skipped", f"{pkg} ja instalado")
        return
    distro = current_distro()
    if distro.immutable:
        announce(
            runner.logger,
            "warning",
            f"sistema imutavel detectado: pulando pacote nativo '{pkg}'. "
            "Prefira a versao Flatpak ou instale manualmente (rpm-ostree/Distrobox).",
        )
        return
    if distro.is_arch:
        runner.run(
            ["pacman", "-S", "--needed", pkg],
            sudo=True,
            action=f"Instalando pacote {pkg}",
            interactive=True,
            interactive_tty=True,
            manual_message="Comando interativo: o pacman pode pedir senha do sudo e confirmacoes.",
        )
        return
    if distro.is_fedora:
        runner.run(
            ["dnf", "install", "-y", pkg],
            sudo=True,
            action=f"Instalando pacote {pkg}",
            interactive=True,
            interactive_tty=True,
            manual_message="Comando interativo: o dnf pode pedir senha do sudo e confirmacoes.",
        )
        return
    if distro.is_debian:
        runner.run(
            ["apt-get", "update"],
            sudo=True,
            action="Atualizando indice de pacotes apt",
            interactive=True,
            interactive_tty=True,
            manual_message="Comando interativo: o apt pode pedir senha do sudo.",
        )
        runner.run(
            ["apt-get", "install", "-y", pkg],
            sudo=True,
            action=f"Instalando pacote {pkg}",
            interactive=True,
            interactive_tty=True,
            manual_message="Comando interativo: o apt pode pedir senha do sudo e confirmacoes.",
        )
        return
    raise UnsupportedDistroError(f"familia de distro nao suportada: {distro.family}")


def install_first_available(packages: tuple[str, ...] | list[str], runner: Runner) -> str | None:
    for pkg in packages:
        if system_installed(pkg):
            announce(runner.logger, "skipped", f"{pkg} ja instalado")
            return pkg
    for pkg in packages:
        if system_package_exists(pkg):
            install_system_package(pkg, runner)
            return pkg
    runner.logger.write(f"{badge('aviso', Color.WARNING)} nao encontrei pacote disponivel entre: {', '.join(packages)}")
    return None


def install_system_or_aur(system_pkg: str, aur_pkg: str | None, runner: Runner) -> bool:
    if system_installed(system_pkg):
        announce(runner.logger, "skipped", f"{system_pkg} ja instalado")
        return True
    if aur_pkg and system_installed(aur_pkg):
        announce(runner.logger, "skipped", f"{aur_pkg} ja instalado")
        return True
    if current_distro().immutable:
        # Em sistemas imutaveis nao instalamos nativo: sinalizamos falha para
        # que o chamador caia no Flatpak.
        announce(
            runner.logger,
            "warning",
            f"sistema imutavel: '{system_pkg}' nao sera instalado de forma nativa; tentarei Flatpak.",
        )
        return False
    if system_package_exists(system_pkg):
        install_system_package(system_pkg, runner)
        return True
    helper = aur_helper()
    if aur_pkg and helper:
        runner.run(
            [helper, "-S", "--needed", aur_pkg],
            action=f"Instalando pacote AUR {aur_pkg}",
            interactive=True,
            interactive_tty=True,
            manual_message="Comando interativo: o helper AUR pode pedir confirmacoes.",
        )
        return True
    runner.logger.write(f"{badge('aviso', Color.WARNING)} nao encontrei pacote para {system_pkg}")
    return False


def installed_packages_matching(needle: str) -> list[str]:
    """Lista pacotes instalados cujo nome contem `needle` (somente leitura).

    Usado para varrer residuos de driver (ex.: todos os pacotes 'nvidia') sem
    depender de uma lista fixa. Degrada para lista vazia se faltar o gerenciador.
    """
    distro = current_distro()
    needle = needle.lower()
    if distro.is_arch:
        if shutil.which("pacman") is None:
            return []
        cmd = ["pacman", "-Qq"]
    elif distro.is_fedora:
        if shutil.which("rpm") is None:
            return []
        cmd = ["rpm", "-qa", "--qf", "%{NAME}\n"]
    elif distro.is_debian:
        if shutil.which("dpkg-query") is None:
            return []
        cmd = ["dpkg-query", "-W", "-f=${Package}\n"]
    else:
        return []
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False)
    if result.returncode != 0:
        return []
    return sorted({line.strip() for line in result.stdout.splitlines() if needle in line.strip().lower()})


def remove_system_packages(pkgs: list[str], runner: Runner, *, purge_deps: bool = True) -> list[str]:
    """Remove pacotes nativos (filtrando os que estao instalados). Retorna os removidos.

    No-op em sistemas imutaveis. No Arch usa `-Rns` (remove deps orfaos e configs)
    quando purge_deps; caso contrario `-R`.
    """
    installed = [pkg for pkg in pkgs if system_installed(pkg)]
    if not installed:
        announce(runner.logger, "skipped", "nenhum pacote a remover (ja ausentes)")
        return []
    distro = current_distro()
    if distro.immutable:
        announce(
            runner.logger,
            "warning",
            f"sistema imutavel: pulando remocao nativa de {', '.join(installed)}. Ajuste via rpm-ostree se necessario.",
        )
        return []
    if distro.is_arch:
        flag = "-Rns" if purge_deps else "-R"
        runner.run(
            ["pacman", flag, *installed],
            sudo=True,
            action=f"Removendo {len(installed)} pacote(s) com pacman",
            interactive=True,
            interactive_tty=True,
            manual_message="Comando interativo: o pacman pode pedir senha do sudo e confirmacao da remocao.",
        )
        return installed
    if distro.is_fedora:
        runner.run(
            ["dnf", "remove", "-y", *installed],
            sudo=True,
            action=f"Removendo {len(installed)} pacote(s) com dnf",
            interactive=True,
            interactive_tty=True,
            manual_message="Comando interativo: o dnf pode pedir senha do sudo e confirmacao da remocao.",
        )
        return installed
    if distro.is_debian:
        runner.run(
            ["apt-get", "remove", "-y", *installed],
            sudo=True,
            action=f"Removendo {len(installed)} pacote(s) com apt",
            interactive=True,
            interactive_tty=True,
            manual_message="Comando interativo: o apt pode pedir senha do sudo e confirmacao da remocao.",
        )
        if purge_deps:
            runner.run(
                ["apt-get", "autoremove", "-y"],
                sudo=True,
                check=False,
                action="Removendo dependencias orfas com apt",
                interactive=True,
                interactive_tty=True,
                manual_message="Comando interativo: o apt pode pedir senha do sudo.",
            )
        return installed
    raise UnsupportedDistroError(f"familia de distro nao suportada: {distro.family}")


def update_system(runner: Runner) -> None:
    distro = current_distro()
    if distro.is_fedora and distro.immutable:
        runner.run(
            ["rpm-ostree", "upgrade"],
            sudo=True,
            action="Atualizando imagem do sistema com rpm-ostree",
            interactive=True,
            interactive_tty=True,
            manual_message="Comando interativo: o rpm-ostree pode pedir senha do sudo. A atualizacao so vale apos reiniciar.",
        )
        return
    if distro.is_arch and distro.immutable:
        announce(
            runner.logger,
            "manual",
            "SteamOS usa imagem read-only: atualize pela interface do SteamOS ou rode 'steamos-update' manualmente.",
        )
        return
    if distro.is_arch:
        install_system_package("pacman-contrib", runner)
        runner.run(
            ["pacman", "-Syu"],
            sudo=True,
            action="Atualizando sistema com pacman",
            interactive=True,
            interactive_tty=True,
            manual_message="Comando interativo: o pacman pode pedir senha do sudo e confirmacoes. Isso nao e travamento.",
        )
        return
    if distro.is_fedora:
        runner.run(
            ["dnf", "upgrade", "--refresh", "-y"],
            sudo=True,
            action="Atualizando sistema com dnf",
            interactive=True,
            interactive_tty=True,
            manual_message="Comando interativo: o dnf pode pedir senha do sudo e confirmacoes. Isso nao e travamento.",
        )
        return
    if distro.is_debian:
        runner.run(
            ["apt-get", "update"],
            sudo=True,
            action="Atualizando indice de pacotes apt",
            interactive=True,
            interactive_tty=True,
            manual_message="Comando interativo: o apt pode pedir senha do sudo.",
        )
        runner.run(
            ["apt-get", "upgrade", "-y"],
            sudo=True,
            action="Atualizando sistema com apt",
            interactive=True,
            interactive_tty=True,
            manual_message="Comando interativo: o apt pode pedir senha do sudo e confirmacoes. Isso nao e travamento.",
        )
        return
    raise UnsupportedDistroError(f"familia de distro nao suportada: {distro.family}")


def pending_updates_command() -> list[str] | str:
    distro = current_distro()
    if distro.is_fedora and distro.immutable:
        return 'rpm-ostree upgrade --check 2>/dev/null; rc=$?; [ "$rc" -eq 0 ] || [ "$rc" -eq 77 ]'
    if distro.is_arch and distro.immutable:
        return "echo 'SteamOS: atualize pela interface do sistema (steamos-update)'"
    if distro.is_arch:
        return 'checkupdates; rc=$?; [ "$rc" -eq 0 ] || [ "$rc" -eq 2 ]'
    if distro.is_fedora:
        return 'dnf check-update 2>/dev/null; rc=$?; [ "$rc" -eq 0 ] || [ "$rc" -eq 100 ]'
    if distro.is_debian:
        return "apt list --upgradable 2>/dev/null | sed '1d'"
    raise UnsupportedDistroError(f"familia de distro nao suportada: {distro.family}")


def system_query_command(*packages: str) -> list[str]:
    distro = current_distro()
    if distro.is_arch:
        return ["pacman", "-Q", *packages]
    if distro.is_fedora:
        return ["rpm", "-q", *packages]
    if distro.is_debian:
        return ["dpkg-query", "-W", *packages]
    raise UnsupportedDistroError(f"familia de distro nao suportada: {distro.family}")


# Memo por processo: no "Aplicar tudo" mais de um step habilita o RPM Fusion
# (GPU e Steam); a checagem/instalacao so precisa acontecer uma vez.
_rpmfusion_ready = False


def _reset_ecosystem_cache() -> None:
    """Zera o memo (para testes)."""
    global _rpmfusion_ready
    _rpmfusion_ready = False


def ensure_rpmfusion(runner: Runner) -> None:
    """Habilita os repositorios RPM Fusion (free/nonfree) no Fedora mutavel.

    Necessario para Steam, codecs e drivers NVIDIA nativos. No-op em outras
    familias e em sistemas imutaveis (onde pacotes nativos sao degradados).
    """
    global _rpmfusion_ready
    distro = current_distro()
    if not distro.is_fedora or distro.immutable:
        return
    if _rpmfusion_ready and not runner.dry_run:
        return
    if system_installed("rpmfusion-free-release") and system_installed("rpmfusion-nonfree-release"):
        announce(runner.logger, "skipped", "RPM Fusion (free/nonfree) ja habilitado")
        _rpmfusion_ready = True
        return
    announce(
        runner.logger,
        "info",
        "Habilitando RPM Fusion (free/nonfree) para liberar Steam, codecs e drivers NVIDIA nativos.",
    )
    cmd = (
        "dnf install https://mirrors.rpmfusion.org/free/fedora/"
        "rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm "
        "https://mirrors.rpmfusion.org/nonfree/fedora/"
        "rpmfusion-nonfree-release-$(rpm -E %fedora).noarch.rpm"
    )
    runner.run(
        cmd,
        sudo=True,
        shell=True,
        action="Habilitando repositorios RPM Fusion",
        interactive=True,
        interactive_tty=True,
        manual_message="Comando interativo: o dnf vai pedir senha do sudo e sua confirmacao para adicionar o RPM Fusion.",
    )
    if not runner.dry_run:
        _rpmfusion_ready = True


# Repositorio oficial do Antigravity no Google Artifact Registry (auto-updater).
_ANTIGRAVITY_APT_KEY_URL = "https://us-central1-apt.pkg.dev/doc/repo-signing-key.gpg"
_ANTIGRAVITY_APT_KEYRING = "/etc/apt/keyrings/antigravity-repo-key.gpg"
_ANTIGRAVITY_APT_LIST = "/etc/apt/sources.list.d/antigravity.list"
_ANTIGRAVITY_APT_LINE = (
    "deb [signed-by=/etc/apt/keyrings/antigravity-repo-key.gpg] "
    "https://us-central1-apt.pkg.dev/projects/antigravity-auto-updater-dev/ antigravity-debian main"
)
_ANTIGRAVITY_YUM_REPO = "/etc/yum.repos.d/antigravity.repo"
_ANTIGRAVITY_YUM_CONTENT = (
    "[antigravity-rpm]\n"
    "name=Antigravity RPM Repository\n"
    "baseurl=https://us-central1-yum.pkg.dev/projects/antigravity-auto-updater-dev/antigravity-rpm\n"
    "enabled=1\n"
    "gpgcheck=0\n"
)


def ensure_antigravity_repo(runner: Runner) -> None:
    """Habilita o repositorio oficial do Antigravity (APT no Debian, YUM no Fedora).

    O repositorio e mantido pelo Google e serve o pacote `antigravity` com
    auto-atualizacao via apt/dnf. No-op em Arch e em sistemas imutaveis (onde a
    etapa cai no tarball em $HOME).
    """
    distro = current_distro()
    if distro.immutable:
        return
    if distro.is_debian:
        if Path(_ANTIGRAVITY_APT_LIST).exists() and Path(_ANTIGRAVITY_APT_KEYRING).exists():
            announce(runner.logger, "skipped", "repositorio APT do Antigravity ja configurado")
            return
        announce(runner.logger, "info", "Adicionando o repositorio oficial (APT) do Antigravity.")
        script = (
            "set -e\n"
            "install -d -m 0755 /etc/apt/keyrings\n"
            f"curl -fsSL {_ANTIGRAVITY_APT_KEY_URL} | gpg --dearmor -o {_ANTIGRAVITY_APT_KEYRING}\n"
            f"printf '%s\\n' {shlex.quote(_ANTIGRAVITY_APT_LINE)} > {_ANTIGRAVITY_APT_LIST}\n"
        )
        runner.run(
            f"bash -c {shlex.quote(script)}",
            sudo=True,
            shell=True,
            action="Configurando repositorio APT do Antigravity",
            interactive=True,
            interactive_tty=True,
            manual_message="Comando interativo: o apt/gpg pode pedir senha do sudo para adicionar o repositorio.",
        )
        return
    if distro.is_fedora:
        write_text_sudo(Path(_ANTIGRAVITY_YUM_REPO), _ANTIGRAVITY_YUM_CONTENT, runner)
        return


# Repositorio oficial do GitHub CLI (usado como fallback quando o pacote 'gh'
# nao esta no repo nativo da distro, tipico em Debian/Ubuntu antigos).
_GH_APT_KEY_URL = "https://cli.github.com/packages/githubcli-archive-keyring.gpg"
_GH_APT_KEYRING = "/etc/apt/keyrings/githubcli-archive-keyring.gpg"
_GH_APT_LIST = "/etc/apt/sources.list.d/github-cli.list"
_GH_APT_LINE = (
    "deb [arch=amd64 signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] "
    "https://cli.github.com/packages stable main"
)
_GH_YUM_REPO_URL = "https://cli.github.com/packages/rpm/gh-cli.repo"


def ensure_github_cli(runner: Runner) -> bool:
    """Instala o GitHub CLI (`gh`). Retorna True se ficou disponivel.

    - Arch: pacote `github-cli`.
    - Fedora/Debian: pacote `gh` do repo nativo; se indisponivel, adiciona o
      repositorio oficial do gh (dnf/apt) e instala de la.
    - Imutaveis (Bazzite/SteamOS): nao instala nativo (retorna False) para o
      chamador orientar Distrobox/Homebrew.
    """
    if command_exists("gh"):
        announce(runner.logger, "skipped", "GitHub CLI (gh) ja instalado")
        return True
    distro = current_distro()
    if distro.immutable:
        announce(
            runner.logger,
            "warning",
            "sistema imutavel: instale o GitHub CLI via Distrobox ou Homebrew "
            "(ex.: 'distrobox enter' e 'sudo dnf install gh', ou 'brew install gh').",
        )
        return False
    if distro.is_arch:
        install_system_package("github-cli", runner)
        return command_exists("gh") or runner.dry_run
    if distro.is_fedora:
        if not system_package_exists("gh"):
            announce(runner.logger, "info", "Adicionando o repositorio oficial (DNF) do GitHub CLI.")
            runner.run(
                ["dnf", "config-manager", "--add-repo", _GH_YUM_REPO_URL],
                sudo=True,
                check=False,
                action="Adicionando repositorio do GitHub CLI",
                interactive=True,
                interactive_tty=True,
                manual_message="Comando interativo: o dnf pode pedir a senha do sudo.",
            )
        install_system_package("gh", runner)
        return command_exists("gh") or runner.dry_run
    if distro.is_debian:
        if not system_package_exists("gh"):
            announce(runner.logger, "info", "Adicionando o repositorio oficial (APT) do GitHub CLI.")
            script = (
                "set -e\n"
                "install -d -m 0755 /etc/apt/keyrings\n"
                f"curl -fsSL {_GH_APT_KEY_URL} -o {_GH_APT_KEYRING}\n"
                f"chmod go+r {_GH_APT_KEYRING}\n"
                f"printf '%s\\n' {shlex.quote(_GH_APT_LINE)} > {_GH_APT_LIST}\n"
            )
            runner.run(
                f"bash -c {shlex.quote(script)}",
                sudo=True,
                shell=True,
                action="Configurando repositorio APT do GitHub CLI",
                interactive=True,
                interactive_tty=True,
                manual_message="Comando interativo: o apt pode pedir a senha do sudo para adicionar o repositorio.",
            )
        install_system_package("gh", runner)
        return command_exists("gh") or runner.dry_run
    raise UnsupportedDistroError(f"familia de distro nao suportada: {distro.family}")
