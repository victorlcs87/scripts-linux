from __future__ import annotations

import os
import re
import subprocess
import time
from functools import partial
from pathlib import Path

from ..core import (
    Color,
    badge,
    capture,
    clean_subprocess_env,
)
from ..desktop import DesktopEntry, install_desktop_entry
from ..installers import (
    copy_asset,
    fetch_json,
)
from ..steps_base import Step, StepTask
from ._common import header


class UpdateAppImagesStep(Step):
    id = "15"
    title = "Atualizar AppImages"
    description = (
        "Instala e atualiza os AppImages geridos (Hydra Launcher e o proprio Reforja) a partir dos "
        "GitHub Releases, criando o atalho .desktop e o icone. Instala o que estiver ausente."
    )

    # Cada entrada: nome exibivel, caminho relativo ao home, função que retorna (url_download, tag_versao)
    # Flags opcionais por entrada:
    #   installable: instala (baixa + cria atalho) quando o AppImage nao esta presente.
    #   self_update: o AppImage pode ser o proprio app rodando; atualiza com seguranca
    #                (baixa para .new e faz mv atomico, sem encerrar/relancar o processo).
    _APPIMAGES: list[dict] = [
        {
            "name": "Hydra Launcher",
            "path": Path("AppImages/HydraLauncher-latest.AppImage"),
            "github_repo": "hydralauncher/hydra",
            "asset_pattern": r"\.AppImage$",
            "desktop_path": Path(".local/share/applications/hydralauncher.desktop"),
            "alt_desktop_paths": (
                Path(".local/share/applications/hydra-launcher.desktop"),
                Path(".local/share/applications/Hydra Launcher.desktop"),
            ),
            "icon_asset": "assets/hydra.png",
            "icon_target": Path(".local/share/icons/hydra-launcher.png"),
            "wm_class": "hydralauncher",
            "categories": ("Game",),
            "installable": True,
        },
        {
            "name": "Reforja",
            "path": Path("AppImages/Reforja-latest.AppImage"),
            "github_repo": "victorlcs87/scripts-linux",
            "asset_pattern": r"\.AppImage$",
            "desktop_path": Path(".local/share/applications/reforja.desktop"),
            "alt_desktop_paths": (),
            "icon_asset": "assets/reforja.png",
            "icon_target": Path(".local/share/icons/reforja.png"),
            "wm_class": "reforja",
            "categories": ("System", "Settings", "Utility"),
            "installable": True,
            "self_update": True,
        },
    ]

    # Preselecao programatica (ex.: opcao "Instalar GUI do Reforja" do menu principal):
    # quando nao-vazia, pula o menu de selecao e processa apenas os nomes listados.
    preselect_names: tuple[str, ...] = ()

    def tasks(self) -> list[StepTask]:
        items = []
        for app in self._APPIMAGES:
            if app["installable"]:
                what = (
                    f"Instala o {app['name']} (AppImage) em ~/{app['path']}, com atalho no menu e icone. "
                    "Rodar de novo baixa a ultima versao publicada no GitHub."
                )
            else:
                what = (
                    f"Atualiza o {app['name']} para a ultima versao do GitHub, se ja estiver instalado. "
                    "Nao instala do zero."
                )
            short = f"{app['name']} · AppImage do GitHub"
            category = (app.get("categories") or ("",))[0].lower()
            items.append(
                StepTask(
                    key=app["name"],
                    label=app["name"],
                    description=what,
                    short_description=short,
                    icon=app.get("icon_asset", ""),
                    category=category,
                    detect=partial(self._version_detail, app),
                    run=partial(self._process_one_task, app),
                    remove=partial(self._remove_appimage, app),
                    detail="nao instalado",
                )
            )
        return items

    def _remove_appimage(self, app: dict) -> None:
        """Apaga o AppImage, o registro de versao, o atalho .desktop e o icone."""
        header(self, f"Remover {app['name']}")
        home = self.ctx.user.home
        appimage_path = home / app["path"]
        targets = [
            appimage_path,
            appimage_path.with_suffix(".version"),
            home / app["desktop_path"],
            home / app["icon_target"],
            *(home / alt for alt in app.get("alt_desktop_paths", ())),
        ]
        for path in targets:
            self.ctx.runner.run(
                ["rm", "-f", str(path)],
                check=False,
                show_progress=False,
                action=f"Removendo {path}",
            )

    def apply(self) -> None:
        header(self, self.title, "Verificando e atualizando AppImages instalados")
        if self.preselect_names and self.selection is None:
            self.selection = tuple(self.preselect_names)
        super().apply()

    def _version_detail(self, app: dict) -> str | bool:
        installed = self._installed_version(app)
        return f"instalado: {installed}" if installed else False

    def _process_one_task(self, app: dict) -> None:
        try:
            self._process_one(app, [], [], [])
        except Exception as exc:
            # Dica especifica do AppImage (permissao, rede, disco) antes de propagar.
            self.add_hint(self._failure_hint(app, exc))
            raise

    def _installed_version(self, app: dict) -> str | None:
        """Retorna a versao registrada do AppImage instalado, ou None se ausente."""
        appimage_path = self.ctx.user.home / app["path"]
        if not appimage_path.exists():
            return None
        version_file = appimage_path.with_suffix(".version")
        if version_file.exists():
            return version_file.read_text(encoding="utf-8").strip() or "versao desconhecida"
        return "versao desconhecida"

    def _choice_label(self, app: dict) -> str:
        version = self._installed_version(app)
        if version is None:
            return f"{app['name']} (nao instalado)"
        return f"{app['name']} (instalado: {version})"

    def _process_one(self, app: dict, updated: list[str], skipped: list[str], missing: list[str]) -> None:
        appimage_path = self._resolve_and_migrate(app)
        if appimage_path is None:
            if app.get("installable"):
                result = self._install_fresh(app)
                if result == "updated":
                    updated.append(app["name"])
                else:  # download nao concluiu
                    missing.append(app["name"])
                return
            self.ctx.logger.write(
                f"{badge(app['name'].lower().replace(' ', '-'), Color.WARNING)} {app['name']}: nao instalado, pulando."
            )
            missing.append(app["name"])
            return
        result = self._update_one(app, appimage_path)
        if result == "updated":
            updated.append(app["name"])
        elif result == "current":
            skipped.append(app["name"])

    def _failure_hint(self, app: dict, exc: Exception) -> str:
        canonical = self.ctx.user.home / app["path"]
        if isinstance(exc, PermissionError):
            return (
                f"Sem permissao de escrita para {app['name']} (ex.: {canonical} ou "
                f"{canonical.parent}). Verifique se algum arquivo pertence ao root: "
                f"`sudo chown -R $USER:$USER {canonical.parent}`."
            )
        return f"{app['name']} nao pode ser atualizado agora: {type(exc).__name__}."

    def _resolve_and_migrate(self, app: dict) -> Path | None:
        """Resolve o caminho do AppImage. Se houver instalacao em local nao-canonico,
        migra para o caminho/atalho padrao. Retorna o caminho canonico ou None."""
        canonical = self.ctx.user.home / app["path"]
        if canonical.exists():
            return canonical

        discovered = self._discover_appimage(app)
        if discovered is None:
            return None

        name = app["name"]
        self.ctx.logger.write(
            f"{badge(name.lower().replace(' ', '-'), Color.INFO)} {name}: instalacao detectada em {discovered}; migrando para {canonical}."
        )
        if self.ctx.runner.dry_run:
            self.ctx.logger.write(
                f"{badge('dry-run', Color.DRY_RUN)} criaria {canonical.parent} e moveria {discovered} para {canonical}"
            )
        else:
            canonical.parent.mkdir(parents=True, exist_ok=True)
        self.ctx.runner.run(
            ["mv", str(discovered), str(canonical)],
            check=False,
            action=f"Movendo {name} para o caminho padrao",
            show_progress=False,
        )

        # Reconcilia o atalho canonico apontando para o caminho padrao.
        self._install_launcher(app, canonical)

        # Em dry-run o arquivo nao foi realmente movido; retorna o canonico mesmo assim
        # para que o restante do fluxo simule a atualizacao.
        return canonical

    def _install_launcher(self, app: dict, appimage_path: Path) -> None:
        """Cria o atalho .desktop + icone do AppImage e remove atalhos nao-canonicos."""
        name = app["name"]
        icon_source = self.ctx.root / app["icon_asset"]
        icon_target = self.ctx.user.home / app["icon_target"]
        desktop_file = self.ctx.user.home / app["desktop_path"]
        copy_asset(icon_source, icon_target, self.ctx.runner)
        entry = DesktopEntry(
            name=name,
            exec_line=f"{appimage_path} %U",
            icon=str(icon_target),
            categories=tuple(app["categories"]),
            startup_wm_class=app["wm_class"],
        )
        install_desktop_entry(desktop_file, entry, self.ctx.runner)

        # Remove atalhos nao-canonicos.
        for relative_path in app["alt_desktop_paths"]:
            alt = self.ctx.user.home / relative_path
            if alt == desktop_file or not alt.exists():
                continue
            if self.ctx.runner.dry_run:
                self.ctx.logger.write(f"{badge('dry-run', Color.DRY_RUN)} removeria atalho nao-canonico {alt}")
            else:
                alt.unlink(missing_ok=True)
                self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} Atalho nao-canonico removido: {alt}")

    def _install_fresh(self, app: dict) -> str | None:
        """Instala um AppImage ausente (quando installable): baixa a versao mais recente,
        cria o atalho/icone. Retorna o status de _update_one ou None se nao instalavel."""
        if not app.get("installable"):
            return None
        name = app["name"]
        canonical = self.ctx.user.home / app["path"]
        self.ctx.logger.write(f"{badge(name.lower().replace(' ', '-'), Color.INFO)} {name}: nao instalado; instalando.")
        if self.ctx.runner.dry_run:
            self.ctx.logger.write(f"{badge('dry-run', Color.DRY_RUN)} criaria {canonical.parent} e instalaria {name}")
        else:
            canonical.parent.mkdir(parents=True, exist_ok=True)
        result = self._update_one(app, canonical)
        if result == "updated":
            self._install_launcher(app, canonical)
        return result

    def _discover_appimage(self, app: dict) -> Path | None:
        """Procura um AppImage existente referenciado pelos atalhos .desktop conhecidos."""
        candidates = [app["desktop_path"], *app["alt_desktop_paths"]]
        for relative_path in candidates:
            desktop_file = self.ctx.user.home / relative_path
            if not desktop_file.exists():
                continue
            text = desktop_file.read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                if not line.startswith("Exec="):
                    continue
                exec_value = line[len("Exec=") :].strip()
                # Remove placeholders de campo do .desktop (%U, %f, ...).
                exec_value = re.sub(r"\s+%[a-zA-Z]", "", exec_value).strip()
                target = Path(exec_value)
                if target.suffix == ".AppImage" and target.exists():
                    return target
        return None

    def _update_one(self, app: dict, appimage_path: Path) -> str:
        name = app["name"]
        repo = app["github_repo"]
        pattern = app["asset_pattern"]
        version_file = appimage_path.with_suffix(".version")

        self.ctx.logger.write(f"\n{badge(name.lower().replace(' ', '-'), Color.INFO)} {name}")

        # Consultar release mais recente no GitHub (execucao real mesmo em dry-run: e apenas leitura)
        self.ctx.logger.write(f"Consultando release mais recente de {name}...")
        release = fetch_json(f"https://api.github.com/repos/{repo}/releases/latest")
        if not isinstance(release, dict):
            self.ctx.logger.write(f"{badge('aviso', Color.WARNING)} Nao foi possivel consultar releases de {name}.")
            return "current"

        latest_tag: str = release.get("tag_name", "")
        assets = release.get("assets", [])
        url = next(
            (a["browser_download_url"] for a in assets if re.search(pattern, a["name"])),
            "",
        )
        if not url:
            self.ctx.logger.write(
                f"{badge('aviso', Color.WARNING)} Nenhum asset AppImage encontrado no release {latest_tag} de {name}."
            )
            return "current"

        # Comparar com versao instalada
        installed_tag = version_file.read_text(encoding="utf-8").strip() if version_file.exists() else ""
        app_ok = appimage_path.exists() and os.access(str(appimage_path), os.X_OK)
        # Compara sem o prefixo "v": quem grava o .version pode ou nao inclui-lo.
        same_version = bool(installed_tag) and installed_tag.lstrip("v") == latest_tag.lstrip("v")
        if same_version and app_ok:
            self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} {name} ja esta na versao mais recente ({latest_tag}).")
            return "current"
        if same_version and not app_ok:
            self.ctx.logger.write(
                f"{badge('aviso', Color.WARNING)} {name} marcado como {latest_tag} mas arquivo ausente ou nao-executavel. Forcando re-download."
            )

        if installed_tag:
            self.ctx.logger.write(f"Atualizando {name}: {installed_tag} → {latest_tag}")
        else:
            self.ctx.logger.write(f"Baixando {name} versao {latest_tag} (sem versao registrada localmente)")

        # Auto-atualizacao: o AppImage pode ser o proprio app rodando. Nao encerramos o
        # processo e baixamos para um arquivo .new que substitui o original via mv atomico
        # (rename cria novo inode; a instancia em execucao mantem o inode antigo ate sair).
        self_update = bool(app.get("self_update"))
        download_path = appimage_path.with_name(appimage_path.name + ".new") if self_update else appimage_path
        was_running = False if self_update else self._kill_if_running(appimage_path, name)

        dl = self.ctx.runner.run(
            ["curl", "-L", url, "-o", str(download_path)],
            check=False,
            action=f"Baixando {name} {latest_tag}",
        )
        if dl is not None and dl.returncode != 0:
            self.ctx.logger.write(
                f"{badge('erro', Color.ERROR)} Falha ao baixar {name} (codigo {dl.returncode}). Versao nao registrada."
            )
            if was_running:
                self._relaunch(appimage_path, name)
            return "current"
        chmod_result = self.ctx.runner.run(
            ["chmod", "+x", str(download_path)],
            check=False,
            action=f"Tornando {name} executavel",
            show_progress=False,
        )
        chmod_ok = chmod_result is None or chmod_result.returncode == 0
        if not chmod_ok:
            self.ctx.logger.write(
                f"{badge('erro', Color.ERROR)} Falha ao tornar {name} executavel. Versao nao registrada."
            )
            if was_running:
                self._relaunch(appimage_path, name)
            return "current"
        if self_update:
            mv_result = self.ctx.runner.run(
                ["mv", "-f", str(download_path), str(appimage_path)],
                check=False,
                action=f"Substituindo {name} pelo novo AppImage",
                show_progress=False,
            )
            if mv_result is not None and mv_result.returncode != 0:
                self.ctx.logger.write(
                    f"{badge('erro', Color.ERROR)} Falha ao instalar o novo {name}. Versao nao registrada."
                )
                return "current"
        if not self.ctx.runner.dry_run:
            version_file.write_text(latest_tag + "\n", encoding="utf-8")
        else:
            self.ctx.logger.write(f"{badge('dry-run', Color.DRY_RUN)} gravaria versao {latest_tag} em {version_file}")
        self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} {name} atualizado para {latest_tag}.")
        if self_update:
            self.ctx.logger.write(
                f"{badge('info', Color.INFO)} A nova versao de {name} sera usada no proximo lancamento."
            )
        elif was_running:
            self._relaunch(appimage_path, name)
        return "updated"

    def _kill_if_running(self, appimage_path: Path, name: str) -> bool:
        """Encerra o processo do AppImage se estiver rodando. Retorna True se estava rodando."""
        result = capture(["pgrep", "-f", str(appimage_path)])
        pids = result.stdout.strip().split() if result.stdout.strip() else []
        if not pids:
            return False
        if self.ctx.runner.dry_run:
            self.ctx.logger.write(
                f"{badge('dry-run', Color.DRY_RUN)} encerraria {name} (PIDs: {', '.join(pids)}) antes de atualizar"
            )
            return True
        self.ctx.logger.write(f"Encerrando {name} (PIDs: {', '.join(pids)}) para atualizar...")
        capture(["kill", "-TERM", *pids])
        time.sleep(2)
        # Garante encerramento caso SIGTERM nao tenha sido suficiente
        still = capture(["pgrep", "-f", str(appimage_path)])
        if still.stdout.strip():
            capture(["kill", "-KILL", *still.stdout.strip().split()])
            time.sleep(1)
        self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} {name} encerrado.")
        return True

    def _relaunch(self, appimage_path: Path, name: str) -> None:
        """Relanca o AppImage em segundo plano apos a atualizacao."""
        if self.ctx.runner.dry_run:
            self.ctx.logger.write(f"{badge('dry-run', Color.DRY_RUN)} relancaria {name} em segundo plano")
            return
        self.ctx.logger.write(f"Relancando {name}...")
        subprocess.Popen(
            [str(appimage_path)],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            env=clean_subprocess_env(),
        )
        self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} {name} relancado em segundo plano.")

    def status(self) -> None:
        header(self, self.title, "Versoes instaladas de AppImages")
        installed: list[str] = []
        missing: list[str] = []
        for app in self._APPIMAGES:
            appimage_path = self.ctx.user.home / app["path"]
            version = self._installed_version(app)
            if version is None:
                missing.append(app["name"])
                self.ctx.logger.write(
                    f"{badge(app['name'].lower().replace(' ', '-'), Color.WARNING)} {app['name']}: nao instalado"
                )
                continue
            installed.append(f"{app['name']} {version}")
            self.ctx.logger.write(
                f"{badge(app['name'].lower().replace(' ', '-'), Color.SUCCESS)} {app['name']}: {version} ({appimage_path})"
            )
        if not missing:
            self.mark_applied(f"AppImages instalados: {', '.join(installed)}.", items=installed)
        elif installed:
            self.mark_pending(
                f"Instalados: {', '.join(installed)}. Faltando: {', '.join(missing)}.",
                missing=missing,
            )
        else:
            self.mark_pending("Nenhum AppImage instalado.", missing=missing)

    def undo(self) -> None:
        self.ctx.logger.write(
            "Nao ha undo para atualizacoes de AppImage. Os arquivos .version podem ser removidos manualmente se necessario."
        )
