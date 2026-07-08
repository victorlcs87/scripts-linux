from __future__ import annotations

from pathlib import Path

from ..core import (
    Color,
    badge,
    command_exists,
    print_lines,
    prompt_user,
)
from ..installers import ensure_flatpak
from ..platform import (
    aur_helper,
    current_distro,
    install_first_available,
    install_system_package,
    system_installed,
    system_package_exists,
    system_query_command,
    update_system,
)
from ..steps_base import Step
from ._common import header


class ShellyStep(Step):
    id = "00"
    title = "Atualizar e preparar o sistema"
    description = (
        "Primeiro atualiza todos os pacotes do sistema (pacman/apt/dnf) e depois prepara a base: "
        "Flatpak + Flathub, suporte a AppImage (FUSE) e um helper AUR quando aplicavel. "
        "Rode antes das demais etapas."
    )

    def apply(self) -> None:
        distro = current_distro()
        self._update_system_first(distro)
        header(self, self.title, "Preparando base de pacotes, Flatpak e suporte AppImage")
        ready_before = self._basic_support_ready()
        if distro.immutable:
            sistema = "SteamOS" if distro.is_arch else "Fedora atomico/Bazzite"
            self.ctx.logger.write(
                f"{badge('info', Color.INFO)} {sistema} (imutavel) detectado. Vou priorizar Flatpak; pacotes nativos serao pulados."
            )
        elif distro.is_arch and not command_exists("shelly"):
            self.ctx.logger.write(
                f"{badge('aviso', Color.WARNING)} Shelly nao encontrado. Vou preparar o suporte pelo sistema mesmo assim."
            )
        elif distro.is_arch:
            self.ctx.logger.write(
                f"{badge('info', Color.INFO)} Shelly CLI detectado com suporte a flatpak, appimage e aur."
            )
        elif distro.is_fedora:
            self.ctx.logger.write(
                f"{badge('info', Color.INFO)} Fedora detectado. Vou usar dnf, RPM Fusion e Flatpak, sem Shelly/AUR."
            )
        else:
            self.ctx.logger.write(
                f"{badge('info', Color.INFO)} Debian/Ubuntu detectado. Vou usar apt e opcoes do sistema, sem Shelly/AUR."
            )
        ensure_flatpak(self.ctx.runner)
        self._ensure_appimage_fuse()
        if distro.is_arch and not distro.immutable:
            self._ensure_aur_helper()
        if self._basic_support_ready():
            if ready_before:
                self.ctx.logger.write(
                    f"{badge('ok', Color.SUCCESS)} Flatpak, flathub e suporte AppImage ja estavam prontos."
                )
                self.mark_skipped("Flatpak, flathub e suporte AppImage ja estavam prontos.")
                self.mark_applied("Flatpak, flathub e suporte AppImage estao prontos.")
            else:
                self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} Ecossistema preparado com sucesso.")
                self.mark_done("Ecossistema base preparado com sucesso.")
                self.mark_applied("Ecossistema base preparado com sucesso.")
            return
        if not distro.is_arch or distro.immutable:
            self.ctx.logger.write(f"{badge('aviso', Color.WARNING)} Ainda faltam requisitos do ecossistema.")
            self.mark_pending("Faltam requisitos do ecossistema.", missing=self._missing_basic_support())
            return
        self.ctx.logger.write(
            f"{badge('aviso', Color.WARNING)} Ainda faltam requisitos. Vou abrir o fallback assistido do Shelly."
        )
        if self.ctx.runner.dry_run:
            self.ctx.logger.write(
                f"{badge('dry-run', Color.DRY_RUN)} abriria Shelly ou Shelly UI para concluir ajustes manuais"
            )
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
            detail="O reforja esta pausado aguardando voce confirmar que terminou a revisao.",
            prompt_label="ENTER",
        )
        self.mark_manual("Etapa dependeu de revisao manual no Shelly.")
        self.mark_attention("Ecossistema dependeu de revisao manual no Shelly.")

    def _update_system_first(self, distro) -> None:
        header(self, "Atualizar sistema", "Atualizando a base do sistema e pacotes instalados")
        update_system(self.ctx.runner)
        if distro.is_arch and distro.immutable:
            self.ctx.logger.write(
                f"{badge('info', Color.INFO)} SteamOS usa imagem read-only: atualize pela interface do sistema (steamos-update)."
            )
        elif distro.immutable:
            self.ctx.logger.write(f"{badge('aviso', Color.WARNING)} Atualizacao via rpm-ostree so vale apos reiniciar.")
        else:
            self.ctx.logger.write(f"{badge('aviso', Color.WARNING)} Reinicie apos atualizacao grande/kernel.")

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
        if distro.immutable:
            lines.append(f"{badge('imutavel', Color.INFO)} sim (pacotes nativos via Flatpak)")
        if distro.is_arch and not distro.immutable:
            lines.extend(
                [
                    f"{badge('shelly', Color.INFO)} {'OK' if command_exists('shelly') else 'ausente'}",
                    f"{badge('shelly-ui', Color.INFO)} {'OK' if command_exists('shelly-ui') else 'ausente'}",
                    f"{badge('cachyos-hello', Color.INFO)} {'OK' if command_exists('cachyos-hello') else 'ausente'}",
                    f"{badge('aur', Color.SUCCESS if helper else Color.WARNING)} {helper or 'ausente'}",
                ]
            )
        print_lines(self.ctx.logger, lines)
        if distro.is_arch and not distro.immutable and command_exists("shelly") and flatpak_ready:
            self.ctx.runner.run(
                ["shelly", "flatpak", "list-remotes"],
                check=False,
                action="Verificando remotes do Shelly",
                show_progress=False,
            )
        if self._basic_support_ready():
            self.mark_applied("Flatpak, flathub e suporte AppImage estao aplicados.")
        else:
            missing = self._missing_basic_support()
            self.mark_pending(f"Faltam componentes do ecossistema: {', '.join(missing)}.", missing=missing)

    def _basic_support_ready(self) -> bool:
        distro = current_distro()
        base_ready = command_exists("flatpak") and self._flathub_ready() and self._appimage_fuse_ready()
        if distro.is_arch and not distro.immutable:
            return base_ready and aur_helper() is not None
        return base_ready

    def _missing_basic_support(self) -> list[str]:
        distro = current_distro()
        missing = []
        if not command_exists("flatpak"):
            missing.append("flatpak")
        if command_exists("flatpak") and not self._flathub_ready():
            missing.append("flathub")
        if not self._appimage_fuse_ready():
            missing.append("suporte AppImage/FUSE")
        if distro.is_arch and not distro.immutable and not aur_helper():
            missing.append("helper AUR")
        return missing

    def _flathub_ready(self) -> bool:
        result = self.ctx.runner.run(
            ["flatpak", "remote-list", "--columns=name"],
            check=False,
            action="Verificando remotes Flatpak",
            show_progress=False,
            quiet_success=True,
        )
        if result and result.stdout:
            return any(line.strip() == "flathub" for line in result.stdout.splitlines())
        return self.ctx.runner.dry_run and command_exists("flatpak")

    def _ensure_appimage_fuse(self) -> None:
        distro = current_distro()
        if distro.is_arch:
            install_first_available(("fuse2",), self.ctx.runner)
        elif distro.is_fedora:
            install_first_available(("fuse", "fuse-libs"), self.ctx.runner)
        else:
            install_first_available(("libfuse2t64", "libfuse2", "fuse"), self.ctx.runner)

    def _appimage_fuse_ready(self) -> bool:
        distro = current_distro()
        if distro.is_arch:
            return system_installed("fuse2")
        if distro.is_fedora:
            return any(system_installed(pkg) for pkg in ("fuse", "fuse-libs"))
        return any(system_installed(pkg) for pkg in ("libfuse2t64", "libfuse2", "fuse"))

    def _ensure_aur_helper(self) -> None:
        if aur_helper():
            self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} AUR helper detectado: {aur_helper()}")
            return
        for candidate in ("paru", "yay"):
            if system_package_exists(candidate):
                install_system_package(candidate, self.ctx.runner)
                if aur_helper():
                    self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} AUR helper preparado: {aur_helper()}")
                    return
        self.ctx.logger.write(f"{badge('aviso', Color.WARNING)} Nao consegui instalar automaticamente um helper AUR.")

    def undo(self) -> None:
        self.ctx.logger.write(
            "Nao ha undo seguro para Flatpak/flathub/fuse2/AUR helper. Se quiser, remova manualmente os componentes preparados."
        )


class LinuxToysStep(Step):
    id = "02"
    title = "Instalar Linux Toys"
    description = "Instala o Linux Toys pelo script oficial (colecao de utilitarios e tweaks para o sistema)."

    def apply(self) -> None:
        header(self, self.title, "Instalando utilitarios do Linux Toys")
        if command_exists("linuxtoys") or system_installed("linuxtoys"):
            self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} Linux Toys ja parece instalado")
            self.mark_skipped("Linux Toys ja parece instalado.")
            return
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
        self.mark_done("Linux Toys instalado.")

    def status(self) -> None:
        header(self, self.title, "Verificando presenca do Linux Toys")
        self.ctx.runner.run(system_query_command("linuxtoys"), check=False)
        self.ctx.runner.run(["linuxtoys", "--help"], check=False)
        if command_exists("linuxtoys") or system_installed("linuxtoys"):
            self.mark_applied("Linux Toys esta instalado.")
        else:
            self.mark_pending("Linux Toys ainda nao esta instalado.", missing=["linuxtoys"])
