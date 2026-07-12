from __future__ import annotations

import os
import re
from pathlib import Path

from .. import hardware
from ..core import (
    Color,
    backup_existing,
    badge,
    command_exists,
    select_many,
    write_text,
    write_text_sudo,
)
from ..platform import (
    install_system_or_aur,
    system_installed,
)
from ..steps_base import Step, StepTask
from ._common import InputGroupMixin, header


class KdeStep(InputGroupMixin, Step):
    id = "09"
    title = "Ajustes KDE"
    description = (
        "Ajustes de desktop num unico menu: gestos de 3 dedos no touchpad (libinput-gestures, "
        "pulado em desktops) e Num Lock fixo na sessao e na tela de login (KDE + SDDM)."
    )
    sddm_file = Path("/etc/sddm.conf.d/10-numlock.conf")

    _ITEMS = (
        "Gestos do touchpad (libinput-gestures)",
        "Fixar Num Lock (KDE + SDDM)",
    )

    def tasks(self) -> list[StepTask]:
        return [
            StepTask(
                key="gestos",
                label=self._ITEMS[0],
                description=(
                    "Instala o libinput-gestures e configura gestos de 3 dedos no touchpad: deslizar para "
                    "cima abre a visao geral das janelas (como no GNOME), para baixo mostra a area de "
                    "trabalho, e para os lados troca de area de trabalho. Adiciona voce ao grupo 'input'."
                ),
                available=hardware.has_touchpad(),
                unavailable_reason="nenhum touchpad detectado nesta maquina",
                detect=self._gestures_ready,
                run=self._run_gestures,
            ),
            StepTask(
                key="numlock",
                label=self._ITEMS[1],
                description=(
                    "Deixa o Num Lock ligado por padrao: no KDE (kcminputrc) e tambem na tela de login, "
                    f"escrevendo {self.sddm_file}."
                ),
                detect=self._numlock_ready,
                run=self._run_numlock,
            ),
        ]

    def apply(self) -> None:
        header(self, self.title, "Gestos do touchpad e Num Lock num unico passo")
        super().apply()

    def _run_gestures(self) -> None:
        # _apply_gestures devolve (status, mensagem); so o 'manual' precisa subir.
        status, message = self._apply_gestures()
        if status == "manual":
            self.mark_manual(message)

    def _run_numlock(self) -> None:
        status, message = self._apply_numlock()
        if status == "manual":
            self.mark_manual(message)

    def _gestures_ready(self) -> bool:
        config_file = self.ctx.user.home / ".config/libinput-gestures.conf"
        return (
            system_installed("libinput-gestures")
            and self._user_in_group("input")
            and self._libinput_gestures_running()
            and self._libinput_config_ready(config_file)
            and (self.ctx.user.home / ".local/bin/kde-gnome-like-overview").exists()
        )

    def _numlock_ready(self) -> bool:
        return self._kde_numlock_ready(self.ctx.user.home / ".config/kcminputrc") and self.sddm_file.exists()

    # ------------------------------------------------------------------ gestos

    def _apply_gestures(self) -> tuple[str, str]:
        if not hardware.has_touchpad():
            self.ctx.logger.write(
                f"{badge('aviso', Color.WARNING)} nenhum touchpad detectado nesta maquina; gestos nao se aplicam a desktops."
            )
            return ("skipped", "Gestos: nao aplicavel (sem touchpad)")
        self.ctx.logger.write(
            "Configuracao principal deste item: libinput-gestures com gestos de 3 dedos para Overview."
        )
        install_system_or_aur("libinput-gestures", "libinput-gestures", self.ctx.runner)
        input_group_ready = self._ensure_input_group()
        if not command_exists("libinput-gestures-setup") and not self.ctx.runner.dry_run:
            self.ctx.logger.write("libinput-gestures-setup nao ficou disponivel apos a instalacao.")
            return ("manual", "Gestos: libinput-gestures nao ficou disponivel apos a instalacao")
        self._write_libinput_config()
        self.ctx.runner.run(
            ["libinput-gestures-setup", "autostart"],
            check=False,
            action="Ativando autostart do libinput-gestures",
            show_progress=False,
        )
        self.ctx.runner.run(
            ["libinput-gestures-setup", "restart"],
            check=False,
            action="Reiniciando libinput-gestures",
            show_progress=False,
        )
        if not input_group_ready:
            return (
                "manual",
                f"Gestos: execute 'sudo gpasswd -a {self.ctx.user.name} input' e faca logout/login",
            )
        return ("done", "Gestos configurados com libinput-gestures")

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
            self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} {conf} ja esta atualizado")
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

    # ------------------------------------------------------------------ num lock

    def _apply_numlock(self) -> tuple[str, str]:
        kde_conf = self.ctx.user.home / ".config/kcminputrc"
        if command_exists("kwriteconfig6"):
            if self._kde_numlock_ready(kde_conf):
                self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} Num Lock do KDE ja esta configurado")
            else:
                backup_existing(kde_conf, self.ctx.runner)
                self.ctx.runner.run(
                    ["kwriteconfig6", "--file", "kcminputrc", "--group", "Keyboard", "--key", "NumLock", "0"],
                    check=False,
                    action="Configurando Num Lock do KDE",
                    show_progress=False,
                )
        else:
            content = self._set_ini_value(
                kde_conf.read_text(encoding="utf-8") if kde_conf.exists() else "", "Keyboard", "NumLock", "0"
            )
            if kde_conf.exists() and kde_conf.read_text(encoding="utf-8", errors="ignore") == content:
                self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} Num Lock do KDE ja esta configurado")
            else:
                backup_existing(kde_conf, self.ctx.runner)
                write_text(kde_conf, content, self.ctx.runner)
        self.ctx.runner.run(
            ["mkdir", "-p", str(self.sddm_file.parent)],
            sudo=True,
            action="Garantindo diretorio de configuracao do SDDM",
            show_progress=False,
        )
        sddm_content = "[General]\nNumlock=on\n"
        if self.sddm_file.exists() and self.sddm_file.read_text(encoding="utf-8", errors="ignore") == sddm_content:
            self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} Num Lock do SDDM ja esta configurado")
        else:
            backup_existing(self.sddm_file, self.ctx.runner, sudo=True)
            write_text_sudo(self.sddm_file, sddm_content, self.ctx.runner)
        self._warn_sddm_conflicts()
        return ("done", "Num Lock ajustado para KDE e SDDM")

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
                self.ctx.logger.write(f"{badge('aviso', Color.WARNING)} Possivel conflito SDDM: {path}")

    # ------------------------------------------------------------------ status / undo

    def status(self) -> None:
        header(self, self.title, "Gestos do touchpad e Num Lock")
        self.ctx.logger.write(f"{badge('desktop', Color.INFO)} {os.environ.get('XDG_CURRENT_DESKTOP', 'desconhecido')}")

        g_state, g_summary, g_attention, g_missing = self._status_gestures()
        n_state, n_summary, n_attention, n_missing = self._status_numlock()

        summary = f"Gestos: {g_summary} | Num Lock: {n_summary}"
        estados = {g_state, n_state}
        if estados == {"applied"}:
            self.mark_applied(summary)
        elif estados == {"pending"}:
            self.mark_pending(summary, missing=g_missing + n_missing)
        else:
            self.mark_attention(summary, attention=g_attention + n_attention + g_missing + n_missing)

    def _status_gestures(self) -> tuple[str, str, list[str], list[str]]:
        if not hardware.has_touchpad():
            self.ctx.logger.write(
                f"{badge('touchpad', Color.WARNING)} nenhum touchpad detectado; gestos nao se aplicam a esta maquina."
            )
            return ("applied", "nao aplicavel (sem touchpad)", [], [])
        package_ready = system_installed("libinput-gestures")
        group_ready = self._user_in_group("input")
        service_ready = self._libinput_gestures_running()
        self.ctx.logger.write(
            f"{badge('libinput-gestures', Color.SUCCESS if package_ready else Color.WARNING)} {'instalado' if package_ready else 'ausente'}"
        )
        self.ctx.logger.write(
            f"{badge('grupo-input', Color.SUCCESS if group_ready else Color.WARNING)} {'ok' if group_ready else 'usuario fora do grupo input'}"
        )
        self.ctx.logger.write(
            f"{badge('servico', Color.SUCCESS if service_ready else Color.WARNING)} {'rodando' if service_ready else 'parado'}"
        )
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
            return ("applied", "aplicados", [], [])
        if package_ready or config_ready:
            attention = []
            if not group_ready:
                attention.append("grupo input")
            if not service_ready:
                attention.append("servico libinput-gestures")
            if not expected_config_ready:
                attention.append("gestos up/down")
            if not helper_ready:
                attention.append("helper overview")
            return ("attention", "parcialmente configurados", attention, [])
        return ("pending", "nao configurados", [], ["libinput-gestures", "arquivo de gestos"])

    def _status_numlock(self) -> tuple[str, str, list[str], list[str]]:
        self.ctx.runner.run(["grep", "-n", "NumLock", str(self.ctx.user.home / ".config/kcminputrc")], check=False)
        if self.sddm_file.exists():
            self.ctx.runner.run(["cat", str(self.sddm_file)], check=False)
        else:
            self.ctx.logger.write(f"Configuracao SDDM ainda ausente: {self.sddm_file}")
        kde_ready = self._kde_numlock_ready(self.ctx.user.home / ".config/kcminputrc")
        sddm_ready = self.sddm_file.exists()
        if kde_ready and sddm_ready:
            return ("applied", "aplicado no KDE e no SDDM", [], [])
        if kde_ready or sddm_ready:
            return ("attention", "aplicado parcialmente (falta KDE ou SDDM)", ["Num Lock parcial"], [])
        return ("pending", "nao aplicado", [], ["KDE Num Lock", "SDDM Num Lock"])

    def undo(self) -> None:
        header(self, self.title, "Removendo gestos e Num Lock gerenciados por esta etapa")
        indices = select_many(
            "O que desfazer?",
            ("Gestos do touchpad (config + helper + servico)", "Num Lock do SDDM (config KDE e preservada)"),
            self.ctx.logger,
            detail="Marque um ou mais. Nada marcado = cancelar.",
        )
        if not indices:
            self.mark_skipped("Nada selecionado.")
            return
        chosen = set(indices)
        feito: list[str] = []
        if 0 in chosen:
            self._undo_gestures()
            feito.append("gestos")
        if 1 in chosen:
            self._undo_numlock()
            feito.append("Num Lock (SDDM)")
        self.mark_done("Desfeito: " + ", ".join(feito))

    def _undo_gestures(self) -> None:
        for path in (
            self.ctx.user.home / ".config/libinput-gestures.conf",
            self.ctx.user.home / ".local/bin/kde-gnome-like-overview",
        ):
            if self.ctx.runner.dry_run:
                self.ctx.logger.write(f"{badge('dry-run', Color.DRY_RUN)} removeria {path}")
            else:
                path.unlink(missing_ok=True)
        if command_exists("libinput-gestures-setup"):
            self.ctx.runner.run(["libinput-gestures-setup", "stop"], check=False)
            self.ctx.runner.run(["libinput-gestures-setup", "autostop"], check=False)
        else:
            self.ctx.logger.write("libinput-gestures nao instalado; nada para parar.")

    def _undo_numlock(self) -> None:
        if self.ctx.runner.dry_run:
            self.ctx.logger.write(f"{badge('dry-run', Color.DRY_RUN)} removeria {self.sddm_file}")
        else:
            self.ctx.runner.run(["rm", "-f", str(self.sddm_file)], sudo=True)
        self.ctx.logger.write(
            "A configuracao KDE do usuario foi preservada; use Configuracoes > Teclado para alterar se quiser."
        )
