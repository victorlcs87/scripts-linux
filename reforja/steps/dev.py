from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from ..core import (
    Color,
    PromptInterruptedError,
    backup_existing,
    badge,
    clean_subprocess_env,
    ensure_owner,
    paint,
    print_lines,
    prompt_user,
    write_text,
)
from ..desktop import DesktopEntry, install_desktop_entry
from ..installers import (
    install_system_package,
)
from ..platform import (
    current_distro,
    ensure_antigravity_repo,
    system_installed,
)
from ..steps_base import Step
from ._common import header


class GitStep(Step):
    id = "06"
    title = "Git / GitHub"
    description = (
        "Instala o Git, clona/atualiza o repositorio base e configura uma ou mais contas GitHub "
        "com chave SSH dedicada (alias no ~/.ssh/config + ssh-agent + orientacao de cadastro)."
    )

    @property
    def ssh_dir(self) -> Path:
        return self.ctx.user.home / ".ssh"

    @property
    def config_file(self) -> Path:
        return self.ssh_dir / "config"

    def _key_path(self, alias: str) -> Path:
        return self.ssh_dir / f"id_ed25519_{alias}"

    def apply(self) -> None:
        header(self, self.title, "Repositorio base + contas GitHub via chave SSH")
        install_system_package("git", self.ctx.runner)
        repo_msg = self._setup_base_repo()
        account_msg = self._configure_accounts()
        if self.ctx.runner.dry_run:
            self.ctx.logger.write(
                f"{Color.YELLOW}[dry-run]{Color.RESET} pediria a URL do repositorio e os dados das contas GitHub"
            )
            self.mark_manual("Dry-run indica solicitacao de URL do repositorio e dados das contas.")
            return
        partes = [p for p in (repo_msg, account_msg) if p]
        if partes:
            self.mark_done(" ".join(partes))
            self.mark_applied(" ".join(partes))
        else:
            self.mark_skipped("Nenhuma acao executada (repositorio e contas pulados).")

    def _setup_base_repo(self) -> str:
        base = self.ctx.user.home / "repositorios"
        target = base / "scripts-linux"
        if base.exists():
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} {base} ja existe")
        else:
            self.ctx.runner.run(["mkdir", "-p", str(base)], action=f"Criando diretorio {base}", show_progress=False)
        if self.ctx.runner.dry_run:
            return ""
        try:
            repo_url = prompt_user(
                "Informe a URL do repositorio scripts-linux (SSH/HTTPS, vazio para pular)",
                self.ctx.logger,
                detail="O clone so continua depois que voce fornecer a URL desejada.",
                prompt_label="Repo URL",
                allow_empty=True,
            ).strip()
        except PromptInterruptedError:
            self.ctx.logger.write(
                f"{Color.YELLOW}AVISO:{Color.RESET} Entrada da URL interrompida. Clone/pull cancelado."
            )
            return ""
        if not repo_url:
            self.ctx.logger.write(f"{Color.YELLOW}AVISO:{Color.RESET} URL vazia, pulando clone.")
            return ""
        if (target / ".git").exists():
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} repositorio ja existe em {target}; atualizando")
            self.ctx.runner.run(
                ["git", "-C", str(target), "pull", "--ff-only"],
                check=False,
                action="Atualizando repositorio scripts-linux",
            )
            return "Repositorio scripts-linux atualizado."
        self.ctx.runner.run(["git", "clone", repo_url, str(target)], action="Clonando repositorio scripts-linux")
        return "Repositorio scripts-linux clonado."

    def _ensure_ssh_dir(self) -> None:
        if self.ctx.runner.dry_run:
            return
        self.ssh_dir.mkdir(parents=True, exist_ok=True)
        self.ssh_dir.chmod(0o700)

    def _configure_accounts(self) -> str:
        if self.ctx.runner.dry_run:
            self.ctx.logger.write(
                f"{Color.YELLOW}[dry-run]{Color.RESET} pediria alias/usuario/nome/email e geraria chave ed25519 por conta"
            )
            return ""
        self._ensure_ssh_dir()
        adicionados: list[str] = []
        while True:
            try:
                alias = prompt_user(
                    "Alias da conta GitHub (vazio para encerrar)",
                    self.ctx.logger,
                    detail="Ex: github-work. Cada conta ganha uma chave SSH dedicada. Deixe vazio para nao adicionar (mais).",
                    prompt_label="Alias",
                    allow_empty=True,
                ).strip()
            except PromptInterruptedError:
                self.add_hint("Configuracao de contas GitHub interrompida pelo usuario.")
                break
            if not alias:
                break
            try:
                ghuser = prompt_user(
                    "Usuario GitHub", self.ctx.logger, prompt_label="Usuario", allow_empty=False
                ).strip()
                gitname = prompt_user(
                    "Nome para commits", self.ctx.logger, prompt_label="Nome", allow_empty=False
                ).strip()
                email = prompt_user("Email", self.ctx.logger, prompt_label="Email", allow_empty=False).strip()
            except PromptInterruptedError:
                self.add_hint(f"Dados da conta '{alias}' interrompidos; conta nao configurada.")
                break
            if self._add_account(alias, ghuser, gitname, email):
                adicionados.append(alias)
        if adicionados:
            return f"Contas GitHub configuradas: {', '.join(adicionados)}."
        return ""

    def _account_block(self, alias: str, key: Path) -> str:
        return (
            f"\nHost {alias}\n    HostName github.com\n    User git\n    IdentityFile {key}\n    IdentitiesOnly yes\n"
        )

    def _add_account(self, alias: str, ghuser: str, gitname: str, email: str) -> bool:
        key = self._key_path(alias)
        if key.exists():
            self.ctx.logger.write(f"{Color.YELLOW}AVISO:{Color.RESET} chave do alias '{alias}' ja existe; pulando.")
            self.add_hint(f"Alias '{alias}' ja possui chave em {key}; remova com 'undo' antes de recriar.")
            return False
        self.ctx.runner.run(
            ["ssh-keygen", "-t", "ed25519", "-C", email, "-f", str(key), "-N", ""],
            action=f"Gerando chave SSH ed25519 para '{alias}'",
        )
        existing = self.config_file.read_text(encoding="utf-8") if self.config_file.exists() else ""
        if f"Host {alias}" not in existing:
            backup_existing(self.config_file, self.ctx.runner)
            write_text(self.config_file, existing + self._account_block(alias, key), self.ctx.runner, mode=0o600)
        self.ctx.runner.run(
            ["ssh-add", str(key)],
            check=False,
            action=f"Adicionando chave de '{alias}' ao ssh-agent",
        )
        pub = key.with_suffix(".pub")
        pub_content = pub.read_text(encoding="utf-8").strip() if pub.exists() else "(chave publica nao encontrada)"
        print_lines(
            self.ctx.logger,
            [
                "",
                paint("====================================", Color.SUCCESS),
                paint(f"COPIE ESTA CHAVE PUBLICA ({alias})", Color.SUCCESS),
                paint("====================================", Color.SUCCESS),
                "",
                pub_content,
                "",
                "Proximos passos:",
                "  1) Acesse https://github.com/settings/keys e clique em 'New SSH Key'.",
                "  2) Cole a chave publica acima e salve.",
                f"  3) Teste a conexao: ssh -T git@{alias}",
                f"     (deve aparecer algo como: Hi {ghuser}! You've successfully authenticated...)",
                f"  4) Para clonar: git clone git@{alias}:USUARIO/REPOSITORIO.git",
                "  5) Dentro do repositorio configure manualmente o autor dos commits:",
                f'       git config user.name "{gitname}"',
                f'       git config user.email "{email}"',
                "",
            ],
        )
        return True

    def _remove_account(self, alias: str) -> bool:
        key = self._key_path(alias)
        pub = key.with_suffix(".pub")
        removeu = False
        for path in (key, pub):
            if path.exists():
                self.ctx.runner.run(["rm", "-f", str(path)], action=f"Removendo {path.name}", show_progress=False)
                removeu = True
        if self.config_file.exists():
            content = self.config_file.read_text(encoding="utf-8")
            new_content = self._strip_host_block(content, alias)
            if new_content != content:
                backup_existing(self.config_file, self.ctx.runner)
                write_text(self.config_file, new_content, self.ctx.runner, mode=0o600)
                removeu = True
        return removeu

    @staticmethod
    def _strip_host_block(content: str, alias: str) -> str:
        lines = content.splitlines(keepends=True)
        result: list[str] = []
        skip = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("Host "):
                hosts = stripped.split()[1:]
                skip = alias in hosts
                if skip:
                    continue
            elif skip and stripped == "":
                continue
            elif skip and not line.startswith((" ", "\t")):
                skip = False
            if not skip:
                result.append(line)
        return "".join(result)

    def status(self) -> None:
        header(self, self.title, "Repositorio base, chaves SSH e contas GitHub")
        self.ctx.runner.run(["git", "--version"], check=False)
        self.ctx.runner.run(
            ["git", "-C", str(self.ctx.user.home / "repositorios/scripts-linux"), "status", "--short", "--branch"],
            check=False,
        )
        repo_ok = (self.ctx.user.home / "repositorios/scripts-linux/.git").exists()

        self.ctx.logger.write("")
        self.ctx.logger.write(paint("===== ~/.ssh/config =====", Color.ACCENT))
        if self.config_file.exists():
            print_lines(self.ctx.logger, self.config_file.read_text(encoding="utf-8").splitlines())
        else:
            self.ctx.logger.write("Arquivo inexistente.")

        keys = sorted(self.ssh_dir.glob("id_ed25519_*.pub")) if self.ssh_dir.exists() else []
        self.ctx.logger.write("")
        self.ctx.logger.write(paint("===== CHAVES POR CONTA =====", Color.ACCENT))
        if keys:
            for pub in keys:
                alias = pub.name[len("id_ed25519_") : -len(".pub")]
                self.ctx.logger.write(f"{badge(alias, Color.SUCCESS)} {pub}")
        else:
            self.ctx.logger.write("Nenhuma chave de conta encontrada.")
        self.ctx.runner.run(["ssh-add", "-l"], check=False, action="Listando chaves no ssh-agent")

        if repo_ok and keys:
            self.mark_applied("Repositorio clonado e contas GitHub configuradas.")
        elif repo_ok:
            self.mark_applied("Repositorio scripts-linux clonado; nenhuma conta SSH configurada.")
        elif keys:
            self.mark_attention(
                "Contas GitHub configuradas, mas repositorio base nao foi clonado.",
                attention=["repositorio scripts-linux"],
            )
        else:
            self.mark_pending(
                "Repositorio nao clonado e nenhuma conta GitHub configurada.",
                missing=["repositorio scripts-linux", "contas GitHub"],
            )

    def undo(self) -> None:
        header(self, self.title, "Remover conta GitHub (chave SSH + bloco no config)")
        if self.ctx.runner.dry_run:
            self.ctx.logger.write(
                f"{Color.YELLOW}[dry-run]{Color.RESET} pediria o alias e removeria chave e bloco do config"
            )
            self.mark_manual("Dry-run indica remocao de conta GitHub.")
            return
        try:
            alias = prompt_user(
                "Alias da conta a remover",
                self.ctx.logger,
                detail="Remove id_ed25519_<alias>(.pub) e o bloco Host correspondente do ~/.ssh/config.",
                prompt_label="Alias",
                allow_empty=False,
            ).strip()
        except PromptInterruptedError:
            self.mark_skipped("Remocao cancelada pelo usuario.")
            return
        if self._remove_account(alias):
            self.mark_done(f"Conta '{alias}' removida.")
        else:
            self.mark_skipped(f"Nada a remover para o alias '{alias}'.")


class AntigravityStep(Step):
    id = "12"
    title = "Google Antigravity IDE"
    description = (
        "Instala e mantem atualizado o Google Antigravity IDE: tarball oficial com auto-update "
        "(Arch/imutaveis) ou repositorio nativo apt/dnf (Debian/Fedora)."
    )
    # Endpoint oficial de auto-update (protocolo estilo VS Code): informa a ultima
    # versao estavel do IDE, a URL do tarball e o sha256 para verificacao de integridade.
    update_api = (
        "https://antigravity-auto-updater-974169037036.us-central1.run.app/api/update/linux-x64/stable/latest"
    )
    # Fallback usado apenas se a API estiver indisponivel.
    url = "https://storage.googleapis.com/antigravity-public/antigravity-hub/2.0.6-5413878570549248/linux-x64/Antigravity.tar.gz"
    version = "2.0.6"
    package = "antigravity"

    def _use_native(self) -> bool:
        distro = current_distro()
        return distro.family in ("debian", "fedora") and not distro.immutable

    def apply(self) -> None:
        if self._use_native():
            self._apply_native()
        else:
            self._apply_tarball()

    # ---- caminho nativo (Debian/Fedora mutaveis) -----------------------------

    def _apply_native(self) -> None:
        distro = current_distro()
        mgr = "apt" if distro.is_debian else "dnf"
        header(self, self.title, f"Instalando/atualizando via repositorio nativo ({mgr})")
        ensure_antigravity_repo(self.ctx.runner)
        if distro.is_debian:
            self.ctx.runner.run(
                ["apt-get", "update"],
                sudo=True,
                action="Atualizando indice de pacotes apt",
                interactive=True,
                interactive_tty=True,
                manual_message="Comando interativo: o apt pode pedir senha do sudo.",
            )
            self.ctx.runner.run(
                ["apt-get", "install", "-y", self.package],
                sudo=True,
                action="Instalando/atualizando Antigravity via apt",
                interactive=True,
                interactive_tty=True,
                manual_message="Comando interativo: o apt pode pedir senha do sudo e confirmacoes.",
            )
        else:
            self.ctx.runner.run(
                ["dnf", "install", "-y", self.package],
                sudo=True,
                action="Instalando/atualizando Antigravity via dnf",
                interactive=True,
                interactive_tty=True,
                manual_message="Comando interativo: o dnf pode pedir senha do sudo e confirmacoes.",
            )
        self.mark_done(f"Antigravity instalado/atualizado via {mgr}.")

    # ---- caminho tarball (Arch / imutaveis) ----------------------------------

    @property
    def _install_dir(self) -> Path:
        return self.ctx.user.home / "Antigravity IDE"

    @property
    def _marker(self) -> Path:
        return self._install_dir / ".antigravity-version"

    def _installed_version(self) -> str | None:
        marker = self._marker
        if marker.exists():
            return marker.read_text(encoding="utf-8", errors="ignore").strip() or None
        return None

    def _write_marker(self, version: str) -> None:
        write_text(self._marker, version + "\n", self.ctx.runner)

    def _fetch_latest(self) -> dict | None:
        """Consulta o endpoint de auto-update. Retorna {name, url, sha256} ou None.

        Leitura pura (roda mesmo em dry-run). Qualquer falha de rede resulta em
        None, para o chamador cair no fallback (self.version / self.url).
        """
        try:
            proc = subprocess.run(
                ["curl", "-fsSL", self.update_api],
                capture_output=True,
                text=True,
                timeout=30,
                env=clean_subprocess_env(),
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return None
        name = data.get("productVersion") or data.get("name")
        url = data.get("url")
        if not name or not url:
            return None
        return {"name": str(name), "url": str(url), "sha256": data.get("sha256hash")}

    def _sha256_ok(self, path: Path, expected: str | None) -> bool:
        if not expected:
            return True
        if not path.exists():
            return False
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest().lower() == expected.lower()

    def _apply_tarball(self) -> None:
        header(self, self.title, "Baixando/atualizando IDE, integrando desktop e comando de terminal")
        for pkg in ("curl", "tar", "desktop-file-utils", "findutils", "coreutils"):
            install_system_package(pkg, self.ctx.runner)

        latest = self._fetch_latest()
        if latest is None:
            self.ctx.logger.write(
                f"{Color.YELLOW}AVISO:{Color.RESET} nao consegui consultar a ultima versao; "
                f"usando fallback {self.version}."
            )
            latest = {"name": self.version, "url": self.url, "sha256": None}
        target_version = latest["name"]

        cache = self.ctx.user.home / ".cache/antigravity-ide"
        tarball = cache / f"Antigravity-IDE-{target_version}.tar.gz"
        install_dir = self._install_dir
        existing_exe = self._find_executable(install_dir) if install_dir.exists() else None
        installed_version = self._installed_version()
        if (
            existing_exe
            and installed_version == target_version
            and self._desktop_ready(existing_exe)
            and self._wrapper_ready(existing_exe)
        ):
            self.ctx.logger.write(
                f"{Color.GREEN}OK:{Color.RESET} Antigravity IDE ja esta na versao mais recente ({target_version})"
            )
            self._path_hint()
            self.mark_skipped(f"Antigravity IDE ja estava atualizado ({target_version}).")
            return

        if installed_version and installed_version != target_version:
            self.ctx.logger.write(f"Atualizando Antigravity IDE: {installed_version} -> {target_version}")

        if not self.ctx.runner.dry_run:
            cache.mkdir(parents=True, exist_ok=True)
        backup_existing(install_dir, self.ctx.runner)
        if tarball.exists() and tarball.stat().st_size > 1024 * 1024 and self._sha256_ok(tarball, latest["sha256"]):
            self.ctx.logger.write(f"{Color.GREEN}OK:{Color.RESET} pacote Antigravity ja esta em cache: {tarball}")
        else:
            self.ctx.runner.run(
                ["curl", "-L", "--fail", "-o", str(tarball), latest["url"]],
                action=f"Baixando Antigravity IDE {target_version}",
            )
            if not self.ctx.runner.dry_run and not self._sha256_ok(tarball, latest["sha256"]):
                raise RuntimeError("sha256 do pacote baixado do Antigravity nao confere")
        tmp = cache / "extract"
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
        self._write_marker(target_version)
        ensure_owner(install_dir, self.ctx.user, self.ctx.runner, recursive=True)
        self._path_hint()
        self.mark_done(f"Antigravity IDE instalado/atualizado ({target_version}).")

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
        install_desktop_entry(
            self.ctx.user.home / ".local/share/applications/antigravity-ide.desktop", entry, self.ctx.runner
        )

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
        if self._use_native():
            self._status_native()
        else:
            self._status_tarball()

    def _status_tarball(self) -> None:
        header(self, self.title)
        install_dir = self._install_dir
        desktop_file = self.ctx.user.home / ".local/share/applications/antigravity-ide.desktop"
        wrapper_file = self.ctx.user.home / ".local/bin/antigravity-ide"
        local_bin = str(self.ctx.user.home / ".local/bin")
        path_ready = local_bin in os.environ.get("PATH", "").split(":")
        installed_version = self._installed_version()
        print_lines(
            self.ctx.logger,
            [
                f"{badge('instalacao', Color.INFO)} {'OK' if install_dir.exists() else 'ausente'} - {install_dir}",
                f"{badge('versao', Color.INFO)} {installed_version or 'desconhecida'}",
                f"{badge('desktop', Color.INFO)} {'OK' if desktop_file.exists() else 'ausente'} - {desktop_file}",
                f"{badge('wrapper', Color.INFO)} {'OK' if wrapper_file.exists() else 'ausente'} - {wrapper_file}",
                f"{badge('path', Color.SUCCESS if path_ready else Color.WARNING)} {'OK' if path_ready else 'ausente'} - {local_bin}",
            ],
        )
        latest = self._fetch_latest()
        if latest and installed_version and latest["name"] != installed_version:
            self.add_hint(f"ha versao nova disponivel: {latest['name']} (rode Aplicar para atualizar).")
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

    def _status_native(self) -> None:
        header(self, self.title)
        distro = current_distro()
        mgr = "apt" if distro.is_debian else "dnf"
        installed = system_installed(self.package)
        print_lines(
            self.ctx.logger,
            [f"{badge('pacote', Color.INFO)} {'instalado' if installed else 'ausente'} - {self.package} ({mgr})"],
        )
        if not installed:
            self.mark_pending(f"Antigravity ainda nao esta instalado (repositorio {mgr}).", missing=["pacote"])
            return
        if self._native_update_available(distro):
            self.add_hint(f"ha atualizacao do Antigravity disponivel via {mgr} (rode Aplicar para atualizar).")
        self.mark_applied(f"Antigravity instalado via {mgr}.")

    def _native_update_available(self, distro) -> bool:
        if distro.is_debian:
            cmd = ["apt", "list", "--upgradable", self.package]
        else:
            cmd = ["dnf", "check-update", self.package]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=clean_subprocess_env())
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False
        if distro.is_debian:
            return self.package in proc.stdout and "upgradable" in proc.stdout
        # dnf check-update: codigo 100 = ha atualizacoes; 0 = nada a fazer.
        return proc.returncode == 100 and self.package in proc.stdout

    def undo(self) -> None:
        if self._use_native():
            self._undo_native()
        else:
            self._undo_tarball()

    def _undo_tarball(self) -> None:
        for path in (
            self.ctx.user.home / ".local/share/applications/antigravity-ide.desktop",
            self.ctx.user.home / ".local/bin/antigravity-ide",
            self._install_dir,
        ):
            if self.ctx.runner.dry_run:
                self.ctx.logger.write(f"{Color.YELLOW}[dry-run]{Color.RESET} removeria {path}")
            elif path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)

    def _undo_native(self) -> None:
        distro = current_distro()
        if distro.is_debian:
            self.ctx.runner.run(
                ["apt-get", "remove", "-y", self.package],
                sudo=True,
                check=False,
                action="Removendo Antigravity via apt",
                interactive=True,
                interactive_tty=True,
                manual_message="Comando interativo: o apt pode pedir senha do sudo.",
            )
            repo_files = (
                "/etc/apt/sources.list.d/antigravity.list",
                "/etc/apt/keyrings/antigravity-repo-key.gpg",
            )
        else:
            self.ctx.runner.run(
                ["dnf", "remove", "-y", self.package],
                sudo=True,
                check=False,
                action="Removendo Antigravity via dnf",
                interactive=True,
                interactive_tty=True,
                manual_message="Comando interativo: o dnf pode pedir senha do sudo.",
            )
            repo_files = ("/etc/yum.repos.d/antigravity.repo",)
        for path in repo_files:
            if self.ctx.runner.dry_run:
                self.ctx.logger.write(f"{Color.YELLOW}[dry-run]{Color.RESET} removeria {path}")
            else:
                self.ctx.runner.run(
                    ["rm", "-f", path],
                    sudo=True,
                    check=False,
                    action=f"Removendo {path}",
                    show_progress=False,
                )
