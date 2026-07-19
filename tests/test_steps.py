import json
import os
import shutil
from dataclasses import replace
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from reforja import hardware
from reforja.cli import (
    choose_step,
    install_reforja_gui,
    main_menu,
    render_run_summary,
    render_status_overview,
    run_all,
    select_and_run,
    step_menu,
)
from reforja.core import Logger, MenuOption, PromptInterruptedError, Runner, StepRunResult, UserInfo
from reforja.steps import (
    ALL_STEPS,
    AntigravityStep,
    AppsStep,
    BackupStep,
    BrowserStep,
    FstabStep,
    GitStep,
    GpuStep,
    HardwareStep,
    KdeStep,
    RcloneStep,
    ShellyStep,
    SunshineStep,
    UpdateAppImagesStep,
)
from reforja.steps.storage import Partition
from reforja.steps_base import Step, StepContext, StepTask


@pytest.fixture(autouse=True)
def _select_all(monkeypatch):
    """Por padrao, o seletor multi-item (select_many) marca TODOS os itens, para
    preservar as assercoes de "instala tudo" dos testes existentes. Testes que
    querem uma selecao especifica podem sobrescrever o patch."""

    def _all(prompt, options, logger, *, detail=None, preselected=()):
        return list(range(len(list(options))))

    # steps_base.select_many atende o apply dirigido por tarefas (todas as etapas);
    # os modulos abaixo ainda chamam select_many direto nos fluxos interativos (undo, fstab).
    monkeypatch.setattr("reforja.steps_base.select_many", _all)
    for module in ("dev", "kde", "storage"):
        monkeypatch.setattr(f"reforja.steps.{module}.select_many", _all)


def make_ctx(tmp_path: Path) -> StepContext:
    user_home = tmp_path / "home"
    user_home.mkdir()
    user = UserInfo(name="tester", home=user_home, uid=1000, gid=1000)
    logger = Logger(tmp_path, "test")
    return StepContext(
        root=Path.cwd(),
        run_dir=tmp_path,
        user=user,
        logger=logger,
        runner=Runner(logger, dry_run=True),
    )


def test_numlock_ini_value_is_updated(tmp_path: Path) -> None:
    step = KdeStep(make_ctx(tmp_path))
    text = "[Keyboard]\nNumLock=2\nRepeatDelay=600\n"

    updated = step._set_ini_value(text, "Keyboard", "NumLock", "0")

    assert "NumLock=0" in updated
    assert "NumLock=2" not in updated
    assert "RepeatDelay=600" in updated


def test_all_steps_use_unique_ids() -> None:
    # Os ids sao internos (nao exibidos) e servem para `reforja step <id>` e wrappers.
    # Precisam ser unicos e no formato NN; podem ter lacunas (etapas fundidas/removidas).
    ids = [step.id for step in ALL_STEPS]

    assert len(ids) == len(set(ids)), "ids de etapas devem ser unicos"
    assert all(step_id.isdigit() and len(step_id) == 2 for step_id in ids)


def test_every_step_has_description() -> None:
    for step in ALL_STEPS:
        assert step.description.strip(), f"etapa {step.title} sem description"


class _AutoInteraction:
    """InteractionProvider que responde tudo de forma segura (seam da GUI), para
    exercitar o apply() de cada etapa em dry-run sem travar no stdin."""

    def ask_text(self, prompt, *, detail=None, prompt_label="Resposta", allow_empty=True) -> str:
        return ""  # caminho vazio -> steps tratam como "pular"

    def ask_secret(self, prompt, *, detail=None, prompt_label="Senha") -> str:
        return "senha-dry-run"

    def confirm_phrase(self, phrase, *, detail=None) -> bool:
        return True

    def choose_many(self, prompt, options, *, detail=None, preselected=()) -> list[int]:
        return list(preselected) if preselected else list(range(len(list(options))))


class _CatalogStep(Step):
    """Etapa de catalogo minima para exercitar a semantica Flathub do motor."""

    id = "99"
    title = "Catalogo de teste"
    description = "etapa de teste"
    skip_if_installed = True

    def __init__(self, ctx, states) -> None:
        super().__init__(ctx)
        self._states = states
        self.executed: list[str] = []

    def tasks(self):
        itens = []
        for key, state in self._states.items():
            itens.append(
                StepTask(
                    key=key,
                    label=key,
                    detect=(lambda s=state: s == "aplicado"),
                    run=(lambda k=key: self.executed.append(k)),
                )
            )
        return itens


def test_catalogo_pula_item_ja_instalado_sem_force(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    step = _CatalogStep(ctx, {"Instalado": "aplicado", "Faltando": "pendente"})
    # Selecao explicita marcando ambos: mesmo assim o ja instalado nao roda.
    step.selection = ("Instalado", "Faltando")
    step.apply()
    assert step.executed == ["Faltando"]


def test_catalogo_reinstala_quando_force(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    step = _CatalogStep(ctx, {"Instalado": "aplicado"})
    step.selection = ("Instalado",)
    step.force_keys = {"Instalado"}
    step.apply()
    assert step.executed == ["Instalado"]


def test_remove_items_roda_so_o_callable_de_remocao(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    removidos: list[str] = []

    class _S(Step):
        id = "99"
        title = "T"
        description = "t"

        def tasks(self):
            t = StepTask(key="A", label="A", detect=lambda: True, run=lambda: None)
            t.remove = lambda: removidos.append("A")
            # Item sem callable de remocao: nunca deve ser tocado por remove_items.
            t2 = StepTask(key="B", label="B", detect=lambda: True, run=lambda: None)
            return [t, t2]

    step = _S(ctx)
    step.remove_items(("A", "B"))
    assert removidos == ["A"]


def test_deteccao_robusta_binario_no_path_para_todos(tmp_path: Path, monkeypatch) -> None:
    """Binario no PATH conta como instalado para qualquer app (nao so casos
    especiais): cobre pacote com nome diferente, install por script, etc."""
    ctx = make_ctx(tmp_path)
    step = AppsStep(ctx)
    # Nada pelo pacote nem flatpak; so o binario do Solaar existe no PATH.
    monkeypatch.setattr("reforja.steps.gaming.system_installed", lambda pkg: False)
    monkeypatch.setattr("reforja.steps.gaming.flatpak_installed", lambda app_id: False)
    monkeypatch.setattr("reforja.steps.gaming.npm_global_installed", lambda pkg: False)
    monkeypatch.setattr("reforja.steps.gaming.command_exists", lambda cmd: cmd == "solaar")
    assert step._detect_install_source("Solaar") == "cli (solaar no PATH)"
    # Um app cujo binario nao esta no PATH continua "nao instalado".
    assert step._detect_install_source("Bitwarden") is None


def test_remove_app_escolhe_flatpak_ou_sistema(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = AppsStep(ctx)
    chamadas: list[tuple[str, object]] = []
    monkeypatch.setattr("reforja.steps.gaming.remove_flatpak", lambda app_id, _r: chamadas.append(("flatpak", app_id)))
    monkeypatch.setattr("reforja.steps.gaming.remove_system_packages", lambda pkgs, _r: chamadas.append(("sys", pkgs)))
    # Discord instalado via flatpak -> remove_flatpak; nao toca em pacote de sistema.
    monkeypatch.setattr("reforja.steps.gaming.flatpak_installed", lambda app_id: app_id == "com.discordapp.Discord")
    monkeypatch.setattr("reforja.steps.gaming.system_installed", lambda pkg: False)
    step._remove_app("Discord")
    assert ("flatpak", "com.discordapp.Discord") in chamadas
    assert not any(kind == "sys" for kind, _ in chamadas)


def test_remove_app_nao_remove_runtime_do_codex(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = AppsStep(ctx)
    removidos_sys: list[list[str]] = []
    monkeypatch.setattr("reforja.steps.gaming.remove_system_packages", lambda pkgs, _r: removidos_sys.append(pkgs))
    monkeypatch.setattr("reforja.steps.gaming.flatpak_installed", lambda app_id: False)
    monkeypatch.setattr("reforja.steps.gaming.system_installed", lambda pkg: True)  # nodejs/npm "instalados"
    monkeypatch.setattr("reforja.steps.gaming.npm_global_installed", lambda pkg: True)
    step._remove_app("Codex CLI")
    # Nunca remove nodejs/npm (runtime compartilhado); so o pacote global via npm.
    assert removidos_sys == []


def test_catalogo_preseleciona_o_que_falta(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    step = _CatalogStep(ctx, {"Instalado": "aplicado", "Faltando": "pendente"})
    tasks = step.plan()
    runnable = [t for t in tasks if t.runnable]
    # A pre-selecao do checkbox marca o pendente, nao o ja aplicado (modelo Flathub).
    preselected = [i for i, t in enumerate(runnable) if t.state in ("pendente", "acao") and t.preselectable]
    assert [runnable[i].key for i in preselected] == ["Faltando"]


def test_all_steps_apply_clean_in_dry_run(tmp_path: Path, monkeypatch) -> None:
    """Fumaca: toda etapa deve concluir o apply() em dry-run sem excecao.

    Prompts sao respondidos por um InteractionProvider falso; chamadas de rede
    (releases do GitHub) sao stubadas para o teste ser deterministico.
    """
    monkeypatch.setattr("reforja.steps.appimage.fetch_json", lambda *a, **k: None)
    monkeypatch.setattr("reforja.steps.dev.fetch_json", lambda *a, **k: None)

    ctx = make_ctx(tmp_path)
    ctx.logger.interaction = _AutoInteraction()

    for step_cls in ALL_STEPS:
        step = step_cls(ctx)
        step.select_all = True  # nao abrir o checkbox proprio; pegar o pre-selecionavel
        try:
            step.apply()
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(f"etapa {step_cls.id} {step_cls.title} falhou no dry-run: {exc!r}") from exc


def test_shelly_step_updates_system_before_preparing(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = ShellyStep(ctx)
    order: list[str] = []
    monkeypatch.setattr("reforja.steps.system.update_system", lambda _runner: order.append("update"))
    monkeypatch.setattr("reforja.steps.system.ensure_flatpak", lambda _runner: order.append("prepare"))
    monkeypatch.setattr("reforja.steps.system.command_exists", lambda name: True)
    monkeypatch.setattr("reforja.steps.system.aur_helper", lambda: "paru")
    monkeypatch.setattr("reforja.steps.system.system_installed", lambda pkg: True)
    monkeypatch.setattr("reforja.steps.system.install_first_available", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "reforja.steps.system.current_distro",
        lambda: type(
            "D",
            (),
            {
                "is_arch": True,
                "is_debian": False,
                "is_fedora": False,
                "immutable": False,
                "id": "cachyos",
                "family": "arch",
            },
        )(),
    )
    monkeypatch.setattr(ctx.runner, "run", lambda *_a, **_k: None)

    step.apply()

    # A atualizacao do sistema roda ANTES da preparacao (ensure_flatpak).
    assert order[0] == "update"
    assert "prepare" in order


def test_sunshine_dry_run_mentions_udev_autostart_ufw_and_desktop(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = SunshineStep(ctx)

    monkeypatch.setattr(
        "reforja.steps.gaming.install_system_package", lambda pkg, runner: runner.logger.write(f"instalaria {pkg}")
    )
    monkeypatch.setattr(
        "reforja.steps.gaming.command_exists", lambda name: name in {"sunshine", "ufw", "update-desktop-database"}
    )
    monkeypatch.setattr(step, "_user_in_group", lambda group: group == "input")
    monkeypatch.setattr(step, "_find_existing_launcher", lambda: None)

    step.apply()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert "instalaria sunshine" in log
    assert "85-sunshine-input.rules" in log
    assert "autostart/sunshine.desktop" in log
    assert ".local/share/applications/sunshine.desktop" in log
    assert "ufw allow 47984:47990/tcp" in log
    assert not step.autostart_file.exists()


def test_sunshine_uses_existing_desktop_launcher_when_package_provides_one(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = SunshineStep(ctx)
    launcher_dir = ctx.user.home / ".local/share/applications"
    launcher_dir.mkdir(parents=True)
    package_launcher = launcher_dir / "org.lizardbyte.sunshine.desktop"
    package_launcher.write_text("[Desktop Entry]\nName=Sunshine\nExec=/usr/bin/sunshine\n", encoding="utf-8")

    step._ensure_menu_launcher()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert "lancador Sunshine ja fornecido" in log
    assert not step.fallback_desktop_file.exists()


def test_sunshine_fallback_desktop_content(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    ctx.runner.dry_run = False
    step = SunshineStep(ctx)

    monkeypatch.setattr("reforja.steps.gaming.command_exists", lambda name: False)
    monkeypatch.setattr(step, "_find_existing_launcher", lambda: None)

    step._ensure_menu_launcher()
    text = step.fallback_desktop_file.read_text(encoding="utf-8")

    assert "Name=Sunshine" in text
    assert "Exec=/usr/bin/sunshine" in text
    assert "Categories=Game;Network;" in text


def test_sunshine_status_applied_when_everything_is_ready(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = SunshineStep(ctx)
    launcher = ctx.user.home / ".local/share/applications/org.lizardbyte.sunshine.desktop"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("[Desktop Entry]\nName=Sunshine\nExec=/usr/bin/sunshine\n", encoding="utf-8")
    step.autostart_file.parent.mkdir(parents=True)
    step.autostart_file.write_text(step._autostart_content(), encoding="utf-8")

    monkeypatch.setattr("reforja.steps.gaming.system_installed", lambda pkg: pkg == "sunshine")
    monkeypatch.setattr("reforja.steps.gaming.command_exists", lambda name: name in {"sunshine", "ufw", "ss"})
    monkeypatch.setattr(step, "_sunshine_running", lambda: True)
    monkeypatch.setattr(step, "_user_in_group", lambda group: group == "input")
    monkeypatch.setattr(step, "_udev_rule_ready", lambda: True)
    monkeypatch.setattr(step, "_ufw_rules_ready", lambda: True)

    step.status()

    assert step.result.compliance == "aplicado"


def test_sunshine_status_attention_when_user_service_exists(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = SunshineStep(ctx)
    launcher = ctx.user.home / ".local/share/applications/org.lizardbyte.sunshine.desktop"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("[Desktop Entry]\nName=Sunshine\nExec=/usr/bin/sunshine\n", encoding="utf-8")
    step.autostart_file.parent.mkdir(parents=True)
    step.autostart_file.write_text(step._autostart_content(), encoding="utf-8")
    step.user_service_file.parent.mkdir(parents=True)
    step.user_service_file.write_text("[Service]\nExecStart=/usr/bin/sunshine\n", encoding="utf-8")

    monkeypatch.setattr("reforja.steps.gaming.system_installed", lambda pkg: pkg == "sunshine")
    monkeypatch.setattr("reforja.steps.gaming.command_exists", lambda name: name in {"sunshine", "ufw", "ss"})
    monkeypatch.setattr(step, "_sunshine_running", lambda: True)
    monkeypatch.setattr(step, "_user_in_group", lambda group: group == "input")
    monkeypatch.setattr(step, "_udev_rule_ready", lambda: True)
    monkeypatch.setattr(step, "_ufw_rules_ready", lambda: True)

    step.status()

    assert step.result.compliance == "atencao"
    assert "sunshine.service" in step.result.summary


def test_sunshine_undo_dry_run_mentions_managed_removals(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = SunshineStep(ctx)

    monkeypatch.setattr("reforja.steps.gaming.command_exists", lambda name: name == "ufw")

    step.undo()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert "pkill -u tester -x sunshine" in log
    assert "autostart/sunshine.desktop" in log
    assert ".local/share/applications/sunshine.desktop" in log
    assert "85-sunshine-input.rules" in log
    assert "ufw delete allow 47984:47990/tcp" in log


def test_shelly_step_dry_run_prepares_stack_without_ui_when_ready(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = ShellyStep(ctx)

    monkeypatch.setattr("reforja.steps.system.command_exists", lambda name: name in {"flatpak", "shelly"})
    monkeypatch.setattr("reforja.steps.system.aur_helper", lambda: "paru")
    monkeypatch.setattr(
        "reforja.steps.system.current_distro",
        lambda: type(
            "Distro",
            (),
            {
                "is_arch": True,
                "is_debian": False,
                "is_fedora": False,
                "immutable": False,
                "id": "cachyos",
                "family": "arch",
            },
        )(),
    )
    monkeypatch.setattr("reforja.steps.system.system_installed", lambda pkg: pkg == "fuse2")
    monkeypatch.setattr("reforja.steps.system.install_first_available", lambda *_args, **_kwargs: None)

    def fake_run(cmd, *_args, **_kwargs):
        if cmd == ["flatpak", "remote-list", "--columns=name"]:

            class Result:
                stdout = "flathub\n"
                returncode = 0

            return Result()
        return None

    monkeypatch.setattr(ctx.runner, "run", fake_run)

    step.apply()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert "Ecossistema" in log or "ja estavam prontos" in log
    assert "abriria Shelly" not in log


def test_ecosystem_step_on_debian_does_not_open_shelly_or_require_aur(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = ShellyStep(ctx)

    monkeypatch.setattr(
        "reforja.steps.system.current_distro",
        lambda: type(
            "Distro",
            (),
            {
                "is_arch": False,
                "is_debian": True,
                "is_fedora": False,
                "immutable": False,
                "id": "ubuntu",
                "family": "debian",
            },
        )(),
    )
    monkeypatch.setattr("reforja.steps.system.command_exists", lambda name: name == "flatpak")
    monkeypatch.setattr("reforja.steps.system.system_installed", lambda pkg: pkg == "libfuse2")
    monkeypatch.setattr("reforja.steps.system.install_first_available", lambda *_args, **_kwargs: None)

    def fake_run(cmd, **_kwargs):
        if cmd == ["flatpak", "remote-list", "--columns=name"]:
            return CompletedProcess(cmd, 0, stdout="flathub\n")
        return None

    monkeypatch.setattr(ctx.runner, "run", fake_run)

    step.apply()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert "Debian/Ubuntu detectado" in log
    assert "Shelly UI" not in log
    # A tarefa do helper AUR aparece, mas explicitamente como indisponivel nesta distro.
    assert "indisponivel: a AUR so existe em Arch/CachyOS mutavel" in log
    assert step.result.compliance == "aplicado"


def test_apps_dry_run_mentions_codex_and_flatpaks(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = AppsStep(ctx)

    monkeypatch.setattr(
        "reforja.steps.gaming.current_distro",
        lambda: type(
            "Distro",
            (),
            {
                "is_arch": True,
                "is_debian": False,
                "is_fedora": False,
                "immutable": False,
                "id": "cachyos",
                "family": "arch",
            },
        )(),
    )
    monkeypatch.setattr("reforja.steps.gaming.system_installed", lambda pkg: False)
    monkeypatch.setattr("reforja.steps.gaming.command_exists", lambda command: False)
    monkeypatch.setattr("reforja.steps.gaming.npm_global_installed", lambda pkg: False)
    # Detecao deterministica: nada instalado -> tudo pendente -> tudo roda no dry-run.
    monkeypatch.setattr("reforja.steps.gaming.flatpak_installed", lambda app_id: False)

    step.apply()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert "@openai/codex" in log
    assert "com.discordapp.Discord" in log
    # Hydra deixou de ser instalado aqui (passou para o passo 15).
    assert "hydra" not in log.lower()


def test_apps_on_debian_use_flatpak_for_heroic_and_zapzap_when_system_package_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
    ctx = make_ctx(tmp_path)
    step = AppsStep(ctx)
    installed_flatpaks: list[str] = []

    monkeypatch.setattr(
        "reforja.steps.gaming.current_distro",
        lambda: type(
            "Distro",
            (),
            {
                "is_arch": False,
                "is_debian": True,
                "is_fedora": False,
                "immutable": False,
                "id": "ubuntu",
                "family": "debian",
            },
        )(),
    )
    monkeypatch.setattr("reforja.installers.install_system_or_aur", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("reforja.installers.install_flatpak", lambda app_id, _runner: installed_flatpaks.append(app_id))

    step._install_system_or_flatpak("heroic-games-launcher", "heroic-games-launcher-bin", "com.heroicgameslauncher.hgl")
    step._install_system_or_flatpak("zapzap", "zapzap", "com.rtosta.zapzap")

    assert installed_flatpaks == ["com.heroicgameslauncher.hgl", "com.rtosta.zapzap"]


def test_apps_on_immutable_fall_back_to_flatpak(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = AppsStep(ctx)
    installed_flatpaks: list[str] = []

    monkeypatch.setattr(
        "reforja.steps.gaming.current_distro",
        lambda: type(
            "Distro",
            (),
            {
                "is_arch": True,
                "is_debian": False,
                "is_fedora": False,
                "immutable": True,
                "id": "steamos",
                "family": "arch",
            },
        )(),
    )
    monkeypatch.setattr("reforja.installers.install_system_or_aur", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("reforja.installers.install_flatpak", lambda app_id, _runner: installed_flatpaks.append(app_id))

    step._install_system_or_flatpak("heroic-games-launcher", "heroic-games-launcher-bin", "com.heroicgameslauncher.hgl")

    assert installed_flatpaks == ["com.heroicgameslauncher.hgl"]


def _patch_distro(monkeypatch, *, immutable: bool) -> None:
    monkeypatch.setattr(
        "reforja.steps.gaming.current_distro",
        lambda: type(
            "Distro",
            (),
            {
                "is_arch": True,
                "is_debian": False,
                "is_fedora": False,
                "immutable": immutable,
                "id": "cachyos",
                "family": "arch",
            },
        )(),
    )


def test_auto_cpufreq_uses_system_package_when_available(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = AppsStep(ctx)
    calls: list[tuple] = []

    _patch_distro(monkeypatch, immutable=False)
    monkeypatch.setattr("reforja.steps.gaming.system_installed", lambda pkg: False)
    monkeypatch.setattr("reforja.steps.gaming.command_exists", lambda command: False)
    monkeypatch.setattr(
        "reforja.steps.gaming.install_system_or_aur",
        lambda *args, **kwargs: calls.append(args) or True,
    )

    step._install_auto_cpufreq()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert calls == [("auto-cpufreq", "auto-cpufreq", ctx.runner)]
    assert "auto-cpufreq --install" in log
    assert "git clone" not in log


def test_auto_cpufreq_falls_back_to_github_installer(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = AppsStep(ctx)

    _patch_distro(monkeypatch, immutable=False)
    monkeypatch.setattr("reforja.steps.gaming.system_installed", lambda pkg: False)
    monkeypatch.setattr("reforja.steps.gaming.command_exists", lambda command: False)
    monkeypatch.setattr("reforja.steps.gaming.install_system_or_aur", lambda *args, **kwargs: False)
    monkeypatch.setattr("reforja.steps.gaming.install_system_package", lambda pkg, runner: None)

    step._install_auto_cpufreq()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert "nao possui Flatpak oficial" in log
    assert "git clone" in log
    assert "auto-cpufreq-installer" in log
    assert "auto-cpufreq --install" in log


def test_auto_cpufreq_skips_github_on_immutable(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = AppsStep(ctx)

    _patch_distro(monkeypatch, immutable=True)
    monkeypatch.setattr("reforja.steps.gaming.system_installed", lambda pkg: False)
    monkeypatch.setattr("reforja.steps.gaming.command_exists", lambda command: False)
    monkeypatch.setattr("reforja.steps.gaming.install_system_or_aur", lambda *args, **kwargs: False)

    step._install_auto_cpufreq()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert "sistema imutavel" in log
    assert "git clone" not in log


def test_install_steam_enables_rpmfusion_on_mutable_fedora(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = AppsStep(ctx)
    rpmfusion_called: list[bool] = []

    monkeypatch.setattr(
        "reforja.steps.gaming.current_distro",
        lambda: type(
            "Distro",
            (),
            {
                "is_arch": False,
                "is_debian": False,
                "is_fedora": True,
                "immutable": False,
                "id": "fedora",
                "family": "fedora",
            },
        )(),
    )
    monkeypatch.setattr("reforja.steps.gaming.ensure_rpmfusion", lambda _runner: rpmfusion_called.append(True))
    monkeypatch.setattr("reforja.steps.gaming.install_system_or_aur", lambda *_args, **_kwargs: True)
    monkeypatch.setattr("reforja.steps.gaming.command_exists", lambda command: False)
    monkeypatch.setattr("reforja.steps.gaming.system_installed", lambda pkg: False)

    step._install_steam()

    assert rpmfusion_called == [True]


def test_install_steam_skips_when_preinstalled_on_immutable(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = AppsStep(ctx)
    flatpaks: list[str] = []

    monkeypatch.setattr(
        "reforja.steps.gaming.current_distro",
        lambda: type(
            "Distro",
            (),
            {
                "is_arch": True,
                "is_debian": False,
                "is_fedora": False,
                "immutable": True,
                "id": "steamos",
                "family": "arch",
            },
        )(),
    )
    monkeypatch.setattr("reforja.steps.gaming.command_exists", lambda command: command == "steam")
    monkeypatch.setattr("reforja.steps.gaming.system_installed", lambda pkg: False)
    monkeypatch.setattr("reforja.steps.gaming.install_flatpak", lambda app_id, _runner: flatpaks.append(app_id))

    step._install_steam()

    assert flatpaks == []


def test_render_run_summary_aggregates_counts(tmp_path: Path) -> None:
    logger = Logger(tmp_path, "test")
    results = [
        StepRunResult("00", "Preparar", "done", "ok", "aplicado", 1.2),
        StepRunResult("01", "Atualizar", "skipped", "skip", "pendente", 0.1),
        StepRunResult("02", "Linux Toys", "manual", "manual", "atencao", 0.3),
        StepRunResult("03", "Browser", "failed", "fail", "atencao", 0.2),
    ]

    render_run_summary(logger, "apply", results, 13, 4.8)
    log = logger.path.read_text(encoding="utf-8")

    assert "Resumo final do fluxo" in log
    assert "[done] 1" in log
    assert "[skipped] 1" in log
    assert "[manual] 1" in log
    assert "[failed] 1" in log


class _RecordingRunner(Runner):
    """Runner que registra os comandos sem executar de fato. Simula um clone
    bem-sucedido criando `<target>/.git`, para o fluxo de add-repo prosseguir."""

    def __init__(self, logger: Logger) -> None:
        super().__init__(logger, dry_run=False)
        self.calls: list[list[str]] = []

    def run(self, cmd, **kwargs):  # type: ignore[override]
        recorded = list(cmd) if not isinstance(cmd, str) else [cmd]
        self.calls.append(recorded)
        if isinstance(cmd, list):
            if cmd[:3] == ["gh", "repo", "clone"] and len(cmd) >= 5:
                Path(cmd[4], ".git").mkdir(parents=True, exist_ok=True)
            elif cmd[:2] == ["git", "clone"] and len(cmd) >= 4:
                Path(cmd[3], ".git").mkdir(parents=True, exist_ok=True)
            elif cmd[:1] == ["ssh-keygen"] and "-f" in cmd:
                key = Path(cmd[cmd.index("-f") + 1])
                key.parent.mkdir(parents=True, exist_ok=True)
                key.write_text("PRIV", encoding="utf-8")
                key.with_suffix(".pub").write_text("ssh-ed25519 AAAA", encoding="utf-8")
        return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")


def test_git_step_menu_empty_is_skipped(tmp_path: Path, monkeypatch) -> None:
    step = GitStep(make_ctx(tmp_path))
    monkeypatch.setattr("reforja.steps_base.select_many", lambda *a, **k: [])

    step.apply()

    assert step.result.status == "skipped"
    assert "Nenhum item marcado" in step.result.message


def test_git_step_add_repo_clones_configures_author_and_records_state(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    runner = _RecordingRunner(ctx.logger)
    ctx.runner = runner
    step = GitStep(ctx)

    # So a acao "Adicionar repositorio" (indice 2) marcada no menu da etapa.
    monkeypatch.setattr("reforja.steps_base.select_many", lambda *a, **k: [2])
    # Sub-menus internos de _add_repos (alias/conta/repo) continuam em dev.select_many.
    monkeypatch.setattr("reforja.steps.dev.select_many", lambda *a, **k: [2])
    # gh ausente no ambiente de teste: sem contas nem listagem de repos.
    monkeypatch.setattr("reforja.steps.dev.command_exists", lambda name: False)

    respostas = {"Repo": "victorlcs87/gsv-calendar", "Nome": "Victor Lima", "Email": "v@x.com", "Outro": "n"}

    def fake_prompt(prompt, logger, *, detail=None, prompt_label="Resposta", allow_empty=True):
        return respostas.get(prompt_label, "")

    monkeypatch.setattr("reforja.steps.dev.prompt_user", fake_prompt)

    step.apply()

    target = ctx.user.home / "repositorios" / "gsv-calendar"
    assert ["gh", "repo", "clone", "victorlcs87/gsv-calendar", str(target)] in runner.calls
    assert ["git", "-C", str(target), "config", "user.name", "Victor Lima"] in runner.calls
    assert ["git", "-C", str(target), "config", "user.email", "v@x.com"] in runner.calls

    state = json.loads((ctx.user.home / ".config" / "reforja" / "git.json").read_text(encoding="utf-8"))
    entry = state["repos"][0]
    assert entry["owner"] == "victorlcs87"
    assert entry["repo"] == "gsv-calendar"
    assert entry["email"] == "v@x.com"
    assert step.result.status == "done"


@pytest.mark.parametrize(
    "spec, owner, repo",
    [
        ("victorlcs87/gsv-calendar", "victorlcs87", "gsv-calendar"),
        ("git@github.com:owner/repo.git", "owner", "repo"),
        ("https://github.com/owner/repo", "owner", "repo"),
        ("https://github.com/owner/repo.git", "owner", "repo"),
    ],
)
def test_git_step_parse_owner_repo_handles_url_and_shorthand(spec: str, owner: str, repo: str) -> None:
    assert GitStep._parse_owner_repo(spec) == (owner, repo)
    assert GitStep._repo_dir_name(spec) == repo


def test_git_step_state_roundtrip_and_forget(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    ctx.runner.dry_run = False  # write_text grava de fato
    step = GitStep(ctx)
    target = ctx.user.home / "repositorios" / "gsv-calendar"

    step._record_repo(target, "victorlcs87", "gsv-calendar", "Victor", "v@x.com")
    assert step._load_state()["repos"][0]["repo"] == "gsv-calendar"

    step._forget_repo(str(target))
    assert step._load_state()["repos"] == []


def test_git_step_logged_accounts_parses_gh_status(tmp_path: Path, monkeypatch) -> None:
    step = GitStep(make_ctx(tmp_path))
    sample = (
        "github.com\n"
        "  ✓ Logged in to github.com account victorlcs87 (keyring)\n"
        "  - Active account: true\n"
        "  ✓ Logged in to github.com account victor-work (keyring)\n"
    )
    monkeypatch.setattr(GitStep, "_gh_query", lambda self, args, **k: sample)

    assert step._logged_accounts() == ["victorlcs87", "victor-work"]


def test_git_step_account_block_has_expected_ssh_config(tmp_path: Path) -> None:
    step = GitStep(make_ctx(tmp_path))
    key = step._key_path("github-work")

    block = step._account_block("github-work", key)

    assert "Host github-work" in block
    assert "HostName github.com" in block
    assert f"IdentityFile {key}" in block
    assert "IdentitiesOnly yes" in block


def test_git_step_login_creates_account_alias(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    runner = _RecordingRunner(ctx.logger)
    ctx.runner = runner
    step = GitStep(ctx)

    monkeypatch.setattr("reforja.steps.dev.command_exists", lambda name: True)
    monkeypatch.setattr(
        GitStep, "_gh_user_field", lambda self, field: {"login": "victorlcs87", "email": "v@x.com"}[field]
    )
    # prompt_user retorna "" -> aceita o alias padrao github-<login>.
    monkeypatch.setattr("reforja.steps.dev.prompt_user", lambda prompt, logger, **k: "")

    msg = step._login_account()

    assert "victorlcs87" in msg and "github-victorlcs87" in msg
    key = step._key_path("github-victorlcs87")
    assert ["ssh-keygen", "-t", "ed25519", "-C", "v@x.com", "-f", str(key), "-N", ""] in runner.calls
    assert ["gh", "ssh-key", "add", str(key) + ".pub", "--title", "reforja-github-victorlcs87"] in runner.calls
    cfg = (ctx.user.home / ".ssh" / "config").read_text(encoding="utf-8")
    assert "Host github-victorlcs87" in cfg
    assert "IdentitiesOnly yes" in cfg


def test_git_step_add_repo_uses_ssh_alias_when_configured(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    runner = _RecordingRunner(ctx.logger)
    ctx.runner = runner
    step = GitStep(ctx)

    ssh = ctx.user.home / ".ssh"
    ssh.mkdir()
    (ssh / "id_ed25519_github-trabalho.pub").write_text("ssh-ed25519 AAAA", encoding="utf-8")

    monkeypatch.setattr("reforja.steps.dev.command_exists", lambda name: False)
    # _maybe_pick_alias -> escolhe o unico alias (indice 0).
    monkeypatch.setattr("reforja.steps.dev.select_many", lambda *a, **k: [0])
    respostas = {"Repo": "owner/proj", "Nome": "V", "Email": "v@x.com", "Outro": "n"}
    monkeypatch.setattr(
        "reforja.steps.dev.prompt_user",
        lambda prompt, logger, **k: respostas.get(k.get("prompt_label"), ""),
    )

    step._add_repos()

    target = ctx.user.home / "repositorios" / "proj"
    assert ["git", "clone", "git@github-trabalho:owner/proj.git", str(target)] in runner.calls
    # Nao deve cair no caminho do gh quando ha alias.
    assert not any(call[:3] == ["gh", "repo", "clone"] for call in runner.calls)


def test_git_step_strip_host_block_removes_only_target_alias(tmp_path: Path) -> None:
    step = GitStep(make_ctx(tmp_path))
    content = (
        "Host github-work\n"
        "    HostName github.com\n"
        "    IdentityFile /home/u/.ssh/id_ed25519_github-work\n"
        "    IdentitiesOnly yes\n"
        "\n"
        "Host github-personal\n"
        "    HostName github.com\n"
        "    IdentityFile /home/u/.ssh/id_ed25519_github-personal\n"
        "    IdentitiesOnly yes\n"
    )

    result = step._strip_host_block(content, "github-work")

    assert "Host github-work" not in result
    assert "id_ed25519_github-work" not in result
    assert "Host github-personal" in result
    assert "id_ed25519_github-personal" in result


def test_render_status_overview_groups_applied_pending_attention(tmp_path: Path) -> None:
    logger = Logger(tmp_path, "test")
    results = [
        StepRunResult(
            "00", "Ecossistema", "done", "Flatpak, flathub, AUR helper e fuse2 estao prontos.", "aplicado", 0.1
        ),
        StepRunResult("07", "Rclone", "manual", "Remote do Google Drive ainda nao foi configurado.", "pendente", 0.1),
        StepRunResult(
            "12",
            "Antigravity",
            "done",
            "Antigravity esta instalado, mas ~/.local/bin ainda nao esta no PATH.",
            "atencao",
            0.1,
        ),
    ]

    render_status_overview(logger, results, 13, 1.2)
    log = logger.path.read_text(encoding="utf-8")

    assert "Resumo inteligente do status" in log
    assert "[aplicado] 1" in log
    assert "[pendente] 1" in log
    assert "[atencao] 1" in log


def test_render_status_overview_shows_items_hints_and_next_steps(tmp_path: Path) -> None:
    logger = Logger(tmp_path, "test")
    results = [
        StepRunResult(
            "00",
            "Ecossistema",
            "done",
            "Flatpak, flathub e AUR helper estao prontos.",
            "aplicado",
            0.1,
            applied_items=["flatpak", "flathub", "yay"],
        ),
        StepRunResult(
            "06",
            "Fstab",
            "done",
            "Bloco esperado ainda nao esta presente no /etc/fstab.",
            "pendente",
            0.1,
            missing_items=["bloco de montagem no fstab"],
            hints=["rode Aplicar para gravar o bloco no fstab"],
        ),
        StepRunResult(
            "12",
            "Antigravity",
            "done",
            "Antigravity esta instalado, mas ~/.local/bin nao esta no PATH.",
            "atencao",
            0.1,
            attention_items=["PATH sem ~/.local/bin"],
        ),
    ]

    render_status_overview(logger, results, 13, 1.2)
    log = logger.path.read_text(encoding="utf-8")

    # Acionaveis primeiro: atencao antes de aplicado no corpo.
    assert log.index("Antigravity") < log.index("Ecossistema")
    # O que foi feito, o que falta e a sugestao aparecem.
    assert "feito: flatpak, flathub, yay" in log
    assert "falta: bloco de montagem no fstab" in log
    assert "atencao: PATH sem ~/.local/bin" in log
    assert "sugestao: rode Aplicar para gravar o bloco no fstab" in log
    # Secao de proximos passos lista so o que nao esta aplicado.
    assert "Proximos passos" in log
    proximos = log[log.index("Proximos passos") :]
    assert "Fstab" in proximos
    assert "Antigravity" in proximos
    assert "Ecossistema" not in proximos


def test_render_status_overview_all_applied_reports_nothing_pending(tmp_path: Path) -> None:
    logger = Logger(tmp_path, "test")
    results = [
        StepRunResult("00", "Ecossistema", "done", "Tudo pronto.", "aplicado", 0.1),
        StepRunResult("01", "Atualizacao", "done", "Sistema atualizado.", "aplicado", 0.1),
    ]

    render_status_overview(logger, results, 13, 0.5)
    log = logger.path.read_text(encoding="utf-8")

    assert "Tudo aplicado" in log
    assert "Proximos passos" not in log


def test_gpu_status_renders_friendly_summary_when_everything_is_ok(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = GpuStep(ctx)

    monkeypatch.setattr("reforja.steps.gaming.command_exists", lambda name: True)
    monkeypatch.setattr("reforja.steps.gaming.os.environ", {"XDG_SESSION_TYPE": "wayland"})

    def fake_run(cmd, *_args, **_kwargs):
        if cmd == ["lspci"]:
            return CompletedProcess(
                cmd,
                0,
                stdout="00:02.0 VGA compatible controller: Intel UHD Graphics 630\n"
                "01:00.0 3D controller: NVIDIA Corporation GA107M [GeForce RTX 3050]\n",
            )
        if cmd == ["glxinfo", "-B"]:
            return CompletedProcess(
                cmd, 0, stdout="direct rendering: Yes\nOpenGL renderer string: Mesa Intel(R) Graphics\n"
            )
        if cmd == ["prime-run", "glxinfo", "-B"]:
            return CompletedProcess(
                cmd,
                0,
                stdout="direct rendering: Yes\nOpenGL renderer string: NVIDIA GeForce RTX 5050 Laptop GPU/PCIe/SSE2\n",
            )
        if cmd == ["nvidia-smi"]:
            return CompletedProcess(cmd, 0, stdout="NVIDIA-SMI 610.43.02\n| 0 NVIDIA GeForce RTX 5050 Laptop GPU |\n")
        if cmd == ["pacman", "-Q", "steam", "heroic-games-launcher"]:
            return CompletedProcess(cmd, 0, stdout="steam 1.0.0.85-7\nheroic-games-launcher-bin 2.22.0-1\n")
        raise AssertionError(cmd)

    monkeypatch.setattr(step, "_run_probe", fake_run)
    monkeypatch.setattr("reforja.steps.gaming.system_installed", lambda pkg: pkg in {"steam", "heroic-games-launcher"})
    monkeypatch.setattr("reforja.steps.gaming.flatpak_installed", lambda app_id: False)

    step.status()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert "Sessao grafica: wayland detectado." in log
    assert "OpenGL (GPU primaria): renderer detectado: Mesa Intel(R) Graphics." in log
    assert "GPU NVIDIA dedicada: renderer detectado: NVIDIA GeForce RTX 5050 Laptop GPU/PCIe/SSE2." in log
    assert "Driver NVIDIA: nvidia-smi respondeu corretamente" in log
    assert "Tudo certo com sessao grafica e GPUs avaliados." in log
    assert "Steam" not in log  # launchers agora sao responsabilidade do passo 10
    assert "direct rendering: Yes" not in log


def test_gpu_status_shows_short_details_when_prime_run_fails(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = GpuStep(ctx)

    monkeypatch.setattr("reforja.steps.gaming.command_exists", lambda name: True)
    monkeypatch.setattr("reforja.steps.gaming.os.environ", {"XDG_SESSION_TYPE": "wayland"})

    def fake_run(cmd, *_args, **_kwargs):
        if cmd == ["lspci"]:
            return CompletedProcess(
                cmd,
                0,
                stdout="00:02.0 VGA compatible controller: Intel UHD Graphics 630\n"
                "01:00.0 3D controller: NVIDIA Corporation GA107M [GeForce RTX 3050]\n",
            )
        if cmd == ["glxinfo", "-B"]:
            return CompletedProcess(
                cmd, 0, stdout="direct rendering: Yes\nOpenGL renderer string: Mesa Intel(R) Graphics\n"
            )
        if cmd == ["prime-run", "glxinfo", "-B"]:
            return CompletedProcess(cmd, 1, stdout="prime-run failed to start discrete GPU backend\n")
        if cmd == ["nvidia-smi"]:
            return CompletedProcess(cmd, 0, stdout="NVIDIA-SMI 610.43.02\n| 0 NVIDIA GeForce RTX 5050 Laptop GPU |\n")
        if cmd == ["pacman", "-Q", "steam", "heroic-games-launcher"]:
            return CompletedProcess(cmd, 0, stdout="steam 1.0.0.85-7\nheroic-games-launcher-bin 2.22.0-1\n")
        raise AssertionError(cmd)

    monkeypatch.setattr(step, "_run_probe", fake_run)
    monkeypatch.setattr("reforja.steps.gaming.system_installed", lambda pkg: pkg in {"steam", "heroic-games-launcher"})
    monkeypatch.setattr("reforja.steps.gaming.flatpak_installed", lambda app_id: False)

    step.status()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert "GPU NVIDIA dedicada: prime-run nao confirmou uso da GPU NVIDIA." in log
    assert "Detalhes: prime-run failed to start discrete GPU backend" in log
    assert "Problema(s) detectado(s): 1 item(ns) exigem revisao." in log


def test_gpu_status_flags_missing_direct_rendering(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = GpuStep(ctx)

    monkeypatch.setattr("reforja.steps.gaming.command_exists", lambda name: True)
    monkeypatch.setattr("reforja.steps.gaming.os.environ", {"XDG_SESSION_TYPE": "wayland"})

    def fake_run(cmd, *_args, **_kwargs):
        if cmd == ["lspci"]:
            return CompletedProcess(
                cmd,
                0,
                stdout="00:02.0 VGA compatible controller: Intel UHD Graphics 630\n"
                "01:00.0 3D controller: NVIDIA Corporation GA107M [GeForce RTX 3050]\n",
            )
        if cmd == ["glxinfo", "-B"]:
            return CompletedProcess(
                cmd, 0, stdout="direct rendering: No\nOpenGL renderer string: Mesa Intel(R) Graphics\n"
            )
        if cmd == ["prime-run", "glxinfo", "-B"]:
            return CompletedProcess(
                cmd,
                0,
                stdout="direct rendering: Yes\nOpenGL renderer string: NVIDIA GeForce RTX 5050 Laptop GPU/PCIe/SSE2\n",
            )
        if cmd == ["nvidia-smi"]:
            return CompletedProcess(cmd, 0, stdout="NVIDIA-SMI 610.43.02\n| 0 NVIDIA GeForce RTX 5050 Laptop GPU |\n")
        if cmd == ["pacman", "-Q", "steam", "heroic-games-launcher"]:
            return CompletedProcess(cmd, 0, stdout="steam 1.0.0.85-7\nheroic-games-launcher-bin 2.22.0-1\n")
        raise AssertionError(cmd)

    monkeypatch.setattr(step, "_run_probe", fake_run)
    monkeypatch.setattr("reforja.steps.gaming.system_installed", lambda pkg: pkg in {"steam", "heroic-games-launcher"})
    monkeypatch.setattr("reforja.steps.gaming.flatpak_installed", lambda app_id: False)

    step.status()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert "OpenGL (GPU primaria): OpenGL basico nao respondeu como esperado." in log
    assert "Detalhes: direct rendering: No OpenGL renderer string: Mesa Intel(R) Graphics" in log


def test_gpu_status_marks_missing_nvidia_smi_as_problem(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = GpuStep(ctx)

    def fake_exists(name: str) -> bool:
        return name != "nvidia-smi"

    monkeypatch.setattr("reforja.steps.gaming.command_exists", fake_exists)
    monkeypatch.setattr("reforja.steps.gaming.os.environ", {"XDG_SESSION_TYPE": "wayland"})

    def fake_run(cmd, *_args, **_kwargs):
        if cmd == ["lspci"]:
            return CompletedProcess(
                cmd,
                0,
                stdout="00:02.0 VGA compatible controller: Intel UHD Graphics 630\n"
                "01:00.0 3D controller: NVIDIA Corporation GA107M [GeForce RTX 3050]\n",
            )
        if cmd == ["glxinfo", "-B"]:
            return CompletedProcess(
                cmd, 0, stdout="direct rendering: Yes\nOpenGL renderer string: Mesa Intel(R) Graphics\n"
            )
        if cmd == ["prime-run", "glxinfo", "-B"]:
            return CompletedProcess(
                cmd,
                0,
                stdout="direct rendering: Yes\nOpenGL renderer string: NVIDIA GeForce RTX 5050 Laptop GPU/PCIe/SSE2\n",
            )
        if cmd == ["pacman", "-Q", "steam", "heroic-games-launcher"]:
            return CompletedProcess(cmd, 0, stdout="steam 1.0.0.85-7\nheroic-games-launcher-bin 2.22.0-1\n")
        raise AssertionError(cmd)

    monkeypatch.setattr(step, "_run_probe", fake_run)
    monkeypatch.setattr("reforja.steps.gaming.system_installed", lambda pkg: pkg in {"steam", "heroic-games-launcher"})
    monkeypatch.setattr("reforja.steps.gaming.flatpak_installed", lambda app_id: False)

    step.status()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert "Driver NVIDIA: nvidia-smi nao esta disponivel." in log
    assert "Problema(s) detectado(s): 1 item(ns) exigem revisao." in log


def test_apps_detect_install_source_prefers_existing_flatpak(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = AppsStep(ctx)

    monkeypatch.setattr("reforja.steps.gaming.system_installed", lambda pkg: False)
    monkeypatch.setattr("reforja.steps.gaming.flatpak_installed", lambda app_id: app_id == "com.discordapp.Discord")
    monkeypatch.setattr("reforja.steps.gaming.command_exists", lambda command: command == "flatpak")
    monkeypatch.setattr("reforja.steps.gaming.npm_global_installed", lambda pkg: False)

    assert step._detect_install_source("Discord") == "flatpak (com.discordapp.Discord)"


def test_apps_apply_nada_selecionado_pula(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = AppsStep(ctx)
    monkeypatch.setattr("reforja.steps_base.select_many", lambda *a, **k: [])
    step.apply()
    assert step.result.status == "skipped"
    assert "@openai/codex" not in ctx.logger.path.read_text(encoding="utf-8")


def test_apps_apply_respeita_selecao_parcial(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = AppsStep(ctx)
    # Seleciona apenas o "Codex CLI" (indice na ordem de step.apps).
    codex_index = list(step.apps).index("Codex CLI")
    monkeypatch.setattr("reforja.steps_base.select_many", lambda *a, **k: [codex_index])
    monkeypatch.setattr("reforja.steps.gaming.system_installed", lambda pkg: False)
    monkeypatch.setattr("reforja.steps.gaming.command_exists", lambda command: False)
    monkeypatch.setattr("reforja.steps.gaming.npm_global_installed", lambda pkg: False)

    step.apply()
    log = ctx.logger.path.read_text(encoding="utf-8")
    assert "@openai/codex" in log  # Codex foi processado
    assert "com.discordapp.Discord" not in log  # Discord (nao selecionado) foi ignorado
    assert step.result.status == "done"
    assert "1 item(ns) processado(s)" in step.result.message


def test_appimages_apply_nada_selecionado_pula(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = UpdateAppImagesStep(ctx)
    monkeypatch.setattr("reforja.steps_base.select_many", lambda *a, **k: [])
    step.apply()
    assert step.result.status == "skipped"


def test_apply_cancelado_no_dialogo_nao_conta_como_executado(tmp_path: Path, monkeypatch) -> None:
    """Cancelar a selecao aborta a etapa; nao pode virar 'nada marcado' e seguir."""
    ctx = make_ctx(tmp_path)
    step = BrowserStep(ctx)

    def cancela(*_a, **_k):
        raise PromptInterruptedError("selecao cancelada pelo usuario")

    monkeypatch.setattr("reforja.steps_base.select_many", cancela)

    with pytest.raises(PromptInterruptedError):
        step.apply()


def test_apply_sem_nada_marcado_nao_se_apresenta_como_trabalho_feito(tmp_path: Path, monkeypatch) -> None:
    """O resumo tem de dizer que nada foi alterado ANTES do estado da maquina."""
    ctx = make_ctx(tmp_path)
    step = BrowserStep(ctx)
    monkeypatch.setattr("reforja.steps_base.select_many", lambda *a, **k: [])

    step.apply()

    assert step.result.status == "skipped"
    assert step.result.summary.startswith("Nenhum item marcado; nada foi alterado.")
    assert "Estado atual:" in step.result.summary


def test_webapp_detectado_mesmo_com_runner_em_dry_run(tmp_path: Path, monkeypatch) -> None:
    """Regressao: a deteccao le via capture(), nao via Runner. Na sondagem do card
    (Runner em dry-run, que devolve None) o WebApp existente tem de ser detectado."""
    ctx = make_ctx(tmp_path)
    ctx.runner.dry_run = True  # como o ProbeWorker da GUI monta o step
    step = BrowserStep(ctx)
    monkeypatch.setattr("reforja.steps.browser.command_exists", lambda cmd: cmd == "firefoxpwa")
    # capture devolve a lista real de perfis do firefoxpwa (com o GSV Calendar).
    monkeypatch.setattr(
        "reforja.steps.browser.capture",
        lambda *a, **k: CompletedProcess(a[0], 0, "GSV Calendar: https://gsv-calendar.vercel.app/", ""),
    )
    assert step._webapp_present("GSV Calendar", "gsv-calendar") is True
    assert step._webapp_present("Inexistente", "nao-existe") is False


def test_rclone_remote_detectado_com_runner_em_dry_run(tmp_path: Path, monkeypatch) -> None:
    """Mesma regressao para o rclone: _remote_ready le via capture, nao via Runner."""
    ctx = make_ctx(tmp_path)
    ctx.runner.dry_run = True
    step = RcloneStep(ctx)
    monkeypatch.setattr("reforja.steps.storage.command_exists", lambda cmd: cmd == "rclone")

    def fake_capture(cmd, **_k):
        if "listremotes" in cmd:
            return CompletedProcess(cmd, 0, "Google Drive:\n", "")
        return CompletedProcess(cmd, 0, "dir1\n", "")  # lsd -> token valido

    monkeypatch.setattr("reforja.steps.storage.capture", fake_capture)
    assert step._remote_ready() is True


def test_apps_manage_bitwarden_and_linuxtoys_but_not_hydra(tmp_path: Path) -> None:
    # Hydra migrou para o passo 15; Bitwarden e Linux Toys agora moram aqui.
    step = AppsStep(make_ctx(tmp_path))
    assert "Hydra Launcher" not in step.apps
    assert step.apps["Bitwarden"]["flatpak_id"] == "com.bitwarden.desktop"
    assert "Linux Toys" in step.apps


def test_hydra_is_installable_in_step15(tmp_path: Path) -> None:
    step = UpdateAppImagesStep(make_ctx(tmp_path))
    hydra = next(app for app in step._APPIMAGES if app["name"] == "Hydra Launcher")
    assert hydra["installable"] is True


def test_step15_installs_hydra_when_missing(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)  # dry-run
    step = UpdateAppImagesStep(ctx)
    fake_fetch, fake_capture, _calls = _fake_github_release("v4.1.0", "hydralauncher-4.1.0.AppImage")
    monkeypatch.setattr("reforja.steps.appimage.fetch_json", fake_fetch)
    monkeypatch.setattr("reforja.steps.appimage.capture", fake_capture)
    monkeypatch.setattr("reforja.steps.appimage.copy_asset", lambda *_a, **_k: None)
    monkeypatch.setattr("reforja.steps.appimage.install_desktop_entry", lambda *_a, **_k: None)

    step.apply()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert "Hydra Launcher: nao instalado; instalando." in log
    assert "atualizado para v4.1.0" in log


def test_step15_failure_on_one_item_does_not_abort(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = UpdateAppImagesStep(ctx)
    processed: list[str] = []
    original = step._process_one

    def flaky(app, updated, skipped, missing):
        processed.append(app["name"])
        if app["name"] == "Hydra Launcher":
            raise PermissionError(13, "Permission denied")
        return original(app, updated, skipped, missing)

    monkeypatch.setattr(step, "_process_one", flaky)
    fake_fetch, fake_capture, _calls = _fake_github_release("v1", "Reforja.AppImage")
    monkeypatch.setattr("reforja.steps.appimage.fetch_json", fake_fetch)
    monkeypatch.setattr("reforja.steps.appimage.capture", fake_capture)
    monkeypatch.setattr("reforja.steps.appimage.copy_asset", lambda *_a, **_k: None)
    monkeypatch.setattr("reforja.steps.appimage.install_desktop_entry", lambda *_a, **_k: None)

    step.apply()
    log = ctx.logger.path.read_text(encoding="utf-8")

    # Ambos os itens foram processados; a falha de um nao abortou a etapa.
    assert processed == ["Hydra Launcher", "Reforja"]
    assert step.result.compliance == "atencao"
    assert "Hydra Launcher" in step.result.attention_items
    assert "Hydra Launcher" in step.result.message
    assert "[erro]" in log
    assert any("chown" in hint for hint in step.result.hints)


def test_update_appimages_migrates_manual_install(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    ctx.runner = Runner(ctx.logger, dry_run=False)
    step = UpdateAppImagesStep(ctx)
    app = step._APPIMAGES[0]

    manual_appimage = ctx.user.home / "Downloads/hydralauncher-3.9.7.AppImage"
    manual_appimage.parent.mkdir(parents=True, exist_ok=True)
    manual_appimage.write_text("bin", encoding="utf-8")
    manual_desktop = ctx.user.home / app["alt_desktop_paths"][1]
    manual_desktop.parent.mkdir(parents=True, exist_ok=True)
    manual_desktop.write_text(
        f"[Desktop Entry]\nName=Hydra Launcher\nExec={manual_appimage} %U\n",
        encoding="utf-8",
    )

    installed_desktops: list[tuple[Path, str]] = []
    monkeypatch.setattr("reforja.steps.appimage.copy_asset", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "reforja.steps.appimage.install_desktop_entry",
        lambda path, entry, _runner: installed_desktops.append((path, entry.render())),
    )

    resolved = step._resolve_and_migrate(app)

    canonical = ctx.user.home / app["path"]
    assert resolved == canonical
    assert canonical.exists()
    assert not manual_appimage.exists()
    assert not manual_desktop.exists()
    assert installed_desktops
    desktop_path, rendered = installed_desktops[0]
    assert desktop_path == ctx.user.home / app["desktop_path"]
    assert f"Exec={canonical} %U" in rendered


def test_update_appimages_returns_none_when_not_installed(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    step = UpdateAppImagesStep(ctx)

    assert step._resolve_and_migrate(step._APPIMAGES[0]) is None


def test_update_appimages_keeps_canonical_install(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    step = UpdateAppImagesStep(ctx)
    app = step._APPIMAGES[0]
    canonical = ctx.user.home / app["path"]
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text("bin", encoding="utf-8")

    assert step._resolve_and_migrate(app) == canonical


def _reforja_entry(step: UpdateAppImagesStep) -> dict:
    return next(app for app in step._APPIMAGES if app["name"] == "Reforja")


def _fake_github_release(tag: str, asset: str):
    payload = {"tag_name": tag, "assets": [{"name": asset, "browser_download_url": f"https://x/{asset}"}]}
    calls: list[list[str]] = []

    def fetch(url, **_kwargs):
        calls.append(["curl", "-fsSL", url])
        return payload

    def fake_capture(cmd, **_kwargs):
        calls.append(list(cmd))
        return CompletedProcess(list(cmd), 0, stdout="", stderr="")

    return fetch, fake_capture, calls


def test_reforja_entry_is_installable_and_self_update(tmp_path: Path) -> None:
    step = UpdateAppImagesStep(make_ctx(tmp_path))
    app = _reforja_entry(step)
    assert app["installable"] is True
    assert app["self_update"] is True
    assert app["github_repo"] == "victorlcs87/scripts-linux"


def test_update_appimages_installs_reforja_when_missing(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)  # dry-run
    step = UpdateAppImagesStep(ctx)
    fake_fetch, fake_capture, _calls = _fake_github_release("v1.0.5", "Reforja-1.0.5-x86_64.AppImage")
    monkeypatch.setattr("reforja.steps.appimage.fetch_json", fake_fetch)
    monkeypatch.setattr("reforja.steps.appimage.capture", fake_capture)
    monkeypatch.setattr("reforja.steps.appimage.copy_asset", lambda *_a, **_k: None)
    monkeypatch.setattr("reforja.steps.appimage.install_desktop_entry", lambda *_a, **_k: None)

    step.apply()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert "Reforja: nao instalado; instalando." in log
    assert "atualizado para v1.0.5" in log


def test_reforja_self_update_uses_temp_and_skips_kill(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)  # dry-run: comandos sao apenas ecoados
    step = UpdateAppImagesStep(ctx)
    app = _reforja_entry(step)
    fake_fetch, fake_capture, calls = _fake_github_release("v1.0.6", "Reforja-1.0.6-x86_64.AppImage")
    monkeypatch.setattr("reforja.steps.appimage.fetch_json", fake_fetch)
    monkeypatch.setattr("reforja.steps.appimage.capture", fake_capture)

    canonical = ctx.user.home / app["path"]
    result = step._update_one(app, canonical)
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert result == "updated"
    # Baixa para .new e faz mv atomico, sem encerrar/relancar o processo.
    assert f"{canonical}.new" in log
    assert "mv -f" in log
    assert "proximo lancamento" in log
    # Nao deve consultar/encerrar processos (pgrep/kill) no modo self_update.
    assert not any(cmd and cmd[0] in {"pgrep", "kill"} for cmd in calls)


def test_gestures_config_includes_up_and_down_swipes(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    step = KdeStep(ctx)
    helper = ctx.user.home / ".local/bin/kde-gnome-like-overview"

    rendered = step._libinput_config_content(helper)

    assert f"gesture swipe up 3 {helper}" in rendered
    assert f"gesture swipe down 3 {helper}" in rendered


def test_gestures_adds_user_to_input_group_when_missing(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = KdeStep(ctx)
    calls: list[tuple[list[str] | str, bool]] = []

    monkeypatch.setattr(step, "_user_in_group", lambda group: False)

    def fake_run(cmd, **kwargs):
        calls.append((cmd, bool(kwargs.get("sudo"))))
        return CompletedProcess(cmd, 0, stdout="")

    monkeypatch.setattr(ctx.runner, "run", fake_run)

    ready = step._ensure_input_group()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert ready is True
    assert calls == [(["gpasswd", "-a", "tester", "input"], True)]
    assert "logout/login" in log


def test_gestures_skips_input_group_when_user_is_already_member(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = KdeStep(ctx)
    calls: list[list[str] | str] = []

    monkeypatch.setattr(step, "_user_in_group", lambda group: True)
    monkeypatch.setattr(ctx.runner, "run", lambda cmd, **_kwargs: calls.append(cmd))

    ready = step._ensure_input_group()

    assert ready is True
    assert calls == []


def test_gestures_reports_manual_command_when_input_group_add_fails(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = KdeStep(ctx)

    monkeypatch.setattr(step, "_user_in_group", lambda group: False)
    monkeypatch.setattr(ctx.runner, "run", lambda cmd, **_kwargs: CompletedProcess(cmd, 1, stdout=""))

    ready = step._ensure_input_group()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert ready is False
    assert "sudo gpasswd -a tester input" in log


def test_gestures_status_reports_missing_package_cleanly(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = KdeStep(ctx)

    monkeypatch.setattr("reforja.hardware.has_touchpad", lambda: True)
    monkeypatch.setattr("reforja.steps.kde.system_installed", lambda pkg: False)
    monkeypatch.setattr("reforja.steps.kde.command_exists", lambda command: False)
    monkeypatch.setattr("reforja.steps.kde.os.environ", {"XDG_CURRENT_DESKTOP": "KDE"})

    step.status()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert "libinput-gestures" in log
    assert "ausente" in log


def test_gestures_status_marks_attention_when_group_or_service_are_missing(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = KdeStep(ctx)

    monkeypatch.setattr("reforja.hardware.has_touchpad", lambda: True)
    monkeypatch.setattr("reforja.steps.kde.system_installed", lambda pkg: pkg == "libinput-gestures")
    monkeypatch.setattr("reforja.steps.kde.command_exists", lambda command: command == "libinput-gestures-setup")
    monkeypatch.setattr(step, "_user_in_group", lambda group: False)
    monkeypatch.setattr(step, "_libinput_gestures_running", lambda: False)
    monkeypatch.setattr("reforja.steps.kde.os.environ", {"XDG_CURRENT_DESKTOP": "KDE"})

    step.status()

    assert step.result.compliance == "atencao"
    assert "grupo input" in step.result.attention_items
    assert "servico libinput-gestures" in step.result.attention_items
    assert "gestos up/down" in step.result.attention_items


# --- fstab (etapa 08) --------------------------------------------------------

FSTAB_BASE = (
    "UUID=root-uuid / btrfs subvol=/@,defaults 0 0\n"
    "UUID=boot-uuid /boot vfat defaults,umask=0077 0 2\n"
    "UUID=swap-uuid swap swap defaults 0 0\n"
)

# Espelha a maquina real: NVMe interno com Windows, SATA interno com BACKUP,
# SSD externo USB e mais a raiz/boot/swap (que nunca podem ser oferecidos).
LSBLK_PARTS = [
    Partition("/dev/nvme0n1p3", "918G", "btrfs", "", "root-uuid", "/", False, "", "Linux filesystem"),
    Partition("/dev/nvme0n1p1", "4G", "vfat", "", "boot-uuid", "/boot", False, "", "EFI System"),
    Partition("/dev/nvme0n1p2", "31,9G", "swap", "swap", "swap-uuid", "[SWAP]", False, "", "Linux swap"),
    # ESP e particao de recuperacao do disco do Windows: nao sao dados do usuario.
    Partition("/dev/nvme1n1p1", "100M", "vfat", "", "esp-uuid", "", False, "Force MP600", "EFI System"),
    Partition(
        "/dev/nvme1n1p4", "828M", "ntfs", "", "rec-uuid", "", False, "Force MP600", "Windows recovery environment"
    ),
    Partition(
        "/dev/nvme1n1p3", "464,8G", "ntfs", "WINDOWS", "win-uuid", "", False, "Force MP600", "Microsoft basic data"
    ),
    Partition("/dev/sda1", "476,9G", "ntfs", "BACKUP", "bkp-uuid", "", False, "SATA3 512GB SSD", "HPFS/NTFS/exFAT"),
    Partition("/dev/sdb1", "1,8T", "ext4", "SSD EXTERNO", "ext-uuid", "", True, "Portable SSD", "Linux filesystem"),
]


def make_fstab_step(tmp_path: Path, *, fstab: str = FSTAB_BASE, parts=None) -> FstabStep:
    """FstabStep apontando para um fstab de teste, com a sondagem de discos stubada."""
    step = FstabStep(make_ctx(tmp_path))
    fstab_file = tmp_path / "fstab"
    fstab_file.write_text(fstab, encoding="utf-8")
    step.fstab_path = fstab_file
    step._probe_partitions = lambda: list(LSBLK_PARTS if parts is None else parts)  # type: ignore[method-assign]
    return step


def test_fstab_candidates_exclude_system_partitions(tmp_path: Path) -> None:
    step = make_fstab_step(tmp_path)

    paths = [part.path for part in step._candidates()]

    # Raiz/boot/swap ja estao no fstab fora do bloco; ESP e recuperacao do Windows
    # sao particoes de servico. So sobram os discos de dados de verdade.
    assert paths == ["/dev/nvme1n1p3", "/dev/sda1", "/dev/sdb1"]


def test_fstab_ext4_gets_commit_and_fsck_but_vfat_does_not(tmp_path: Path) -> None:
    step = make_fstab_step(tmp_path)
    ext4 = Partition("/dev/sdc1", "1T", "ext4", "DADOS", "e-uuid", "", False, "", "Linux filesystem")
    exfat = Partition("/dev/sdc2", "1T", "exfat", "TROCA", "x-uuid", "", False, "", "Microsoft basic data")

    linha_ext4 = step._build_lines([replace(ext4, mountpoint="/mnt/dados")])[0]
    linha_exfat = step._build_lines([replace(exfat, mountpoint="/mnt/troca")])[0]

    # commit= so existe no ext2/3/4; passar isso num exfat faria o mount recusar.
    assert "commit=60" in linha_ext4 and linha_ext4.endswith(" 0 2")
    assert "commit=60" not in linha_exfat and linha_exfat.endswith(" 0 0")


def test_fstab_removable_uses_automount_and_nofail(tmp_path: Path) -> None:
    step = make_fstab_step(tmp_path)
    externo = next(part for part in step._candidates() if part.removable)

    line = step._build_lines([replace(externo, mountpoint="/mnt/ssd-externo")])[0]

    assert line.startswith("UUID=ext-uuid /mnt/ssd-externo ext4 ")
    # Boot nunca espera nem quebra sem o disco; monta sozinho no primeiro acesso.
    assert "noauto" in line
    assert "nofail" in line
    assert "x-systemd.automount" in line
    assert line.endswith(" 0 0")


def test_fstab_internal_ntfs_keeps_windows_options(tmp_path: Path) -> None:
    step = make_fstab_step(tmp_path)
    windows = next(part for part in step._candidates() if part.label == "WINDOWS")

    line = step._build_lines([replace(windows, mountpoint="/mnt/windows")])[0]

    assert "uid=1000,gid=1000,umask=022,windows_names" in line
    assert "nofail" in line
    assert "x-systemd.automount" not in line


def test_fstab_suggested_mountpoint_is_slug_of_label(tmp_path: Path) -> None:
    step = make_fstab_step(tmp_path)
    externo = next(part for part in step._candidates() if part.label == "SSD EXTERNO")

    assert step._mountpoint(externo, {}) == "/mnt/ssd-externo"
    # Um mountpoint ja escolhido antes prevalece sobre a sugestao.
    assert step._mountpoint(externo, {"ext-uuid": "/mnt/jogos"}) == "/mnt/jogos"


def test_fstab_preselects_entries_already_in_block(tmp_path: Path, monkeypatch) -> None:
    fstab = (
        FSTAB_BASE + "\n# BEGIN pos-formatacao-cachyos\n"
        "UUID=bkp-uuid /mnt/backup ntfs rw,nofail 0 0\n"
        "# END pos-formatacao-cachyos\n"
    )
    step = make_fstab_step(tmp_path, fstab=fstab)
    capturado: dict[str, object] = {}

    def fake_select(prompt, options, logger, *, detail=None, preselected=()):
        capturado["options"] = list(options)
        capturado["preselected"] = list(preselected)
        return list(preselected)

    monkeypatch.setattr("reforja.steps.storage.select_many", fake_select)
    candidates = step._candidates()

    selection = step._select_partitions(candidates, step._managed_entries())

    # /dev/sda1 (BACKUP) e o indice 1 entre os candidatos e ja esta no bloco.
    assert capturado["preselected"] == [1]
    assert [part.path for part in selection] == ["/dev/sda1"]
    assert selection[0].mountpoint == "/mnt/backup"
    assert "(ja no fstab)" in capturado["options"][1]
    assert "[externo]" in capturado["options"][2]


def test_fstab_preselects_every_candidate_on_first_run(tmp_path: Path, monkeypatch) -> None:
    step = make_fstab_step(tmp_path)  # sem bloco no fstab
    capturado: dict[str, object] = {}

    def fake_select(prompt, options, logger, *, detail=None, preselected=()):
        capturado["preselected"] = list(preselected)
        return []

    monkeypatch.setattr("reforja.steps.storage.select_many", fake_select)
    step._select_partitions(step._candidates(), {})

    # O que sobra de _candidates() ja e so disco de dados: o normal e montar todos.
    assert capturado["preselected"] == [0, 1, 2]


def test_fstab_windows_filesystems_get_user_ownership(tmp_path: Path) -> None:
    step = make_fstab_step(tmp_path)
    exfat = Partition("/dev/sdc2", "1T", "exfat", "TROCA", "x-uuid", "", False, "", "Microsoft basic data")
    ntfs = next(part for part in step._candidates() if part.label == "WINDOWS")

    linha_exfat = step._build_lines([replace(exfat, mountpoint="/mnt/troca")])[0]
    linha_ntfs = step._build_lines([replace(ntfs, mountpoint="/mnt/windows")])[0]

    # Sem dono POSIX no FS, quem diz de quem sao os arquivos e a linha do fstab.
    assert "uid=1000,gid=1000,umask=022" in linha_exfat
    assert "windows_names" not in linha_exfat  # so faz sentido no NTFS
    assert "uid=1000,gid=1000,umask=022,windows_names" in linha_ntfs


def test_fstab_lines_are_visible_and_user_mountable(tmp_path: Path) -> None:
    step = make_fstab_step(tmp_path)

    for part in step._candidates():
        options = step._build_lines([replace(part, mountpoint="/mnt/x")])[0].split()[3].split(",")

        # x-gvfs-show: aparece no Dolphin. users: monta/desmonta sem senha do polkit.
        assert "x-gvfs-show" in options
        assert "users" in options
        # `users` implica noexec: sem o exec DEPOIS dele, nada roda a partir do disco.
        assert options.index("exec") > options.index("users")


def test_fstab_apply_releases_udisks_mount_before_mounting(tmp_path: Path, monkeypatch) -> None:
    # Disco que o Dolphin ja montou: enquanto estiver la, o mount -a nao monta em /mnt.
    step = make_fstab_step(
        tmp_path,
        parts=[replace(p, mountpoint="/run/media/tester/BACKUP") if p.uuid == "bkp-uuid" else p for p in LSBLK_PARTS],
    )
    calls = iter([[1], []])  # marca so o BACKUP
    monkeypatch.setattr("reforja.steps.storage.select_many", lambda *a, **k: next(calls))
    monkeypatch.setattr("reforja.steps.storage.write_text_sudo", lambda *a, **k: None)

    step.apply()
    log = step.ctx.logger.path.read_text(encoding="utf-8")

    assert "udisksctl unmount -b /dev/sda1" in log
    assert log.index("udisksctl unmount") < log.index("sudo mount -a")


def test_fstab_apply_removes_mountpoint_that_left_the_block(tmp_path: Path, monkeypatch) -> None:
    fstab = (
        FSTAB_BASE + "\n# BEGIN pos-formatacao-cachyos\n"
        "UUID=bkp-uuid /mnt/backup ntfs rw,nofail 0 0\n"
        "# END pos-formatacao-cachyos\n"
    )
    step = make_fstab_step(tmp_path, fstab=fstab)
    calls = iter([[0], []])  # troca o BACKUP pelo WINDOWS
    monkeypatch.setattr("reforja.steps.storage.select_many", lambda *a, **k: next(calls))
    monkeypatch.setattr("reforja.steps.storage.write_text_sudo", lambda *a, **k: None)

    step.apply()
    log = step.ctx.logger.path.read_text(encoding="utf-8")

    # rmdir (nunca rm -rf) e so no ponto que saiu do bloco.
    assert "sudo rmdir /mnt/backup" in log
    assert "rmdir /mnt/windows" not in log
    assert "rm -rf" not in log


def test_fstab_is_mounted_asks_findmnt_a_question_it_accepts(tmp_path: Path, monkeypatch) -> None:
    step = make_fstab_step(tmp_path)
    visto: dict[str, list[str]] = {}

    def fake_capture(cmd, **kwargs):
        visto["cmd"] = list(cmd)
        return CompletedProcess(cmd, 0, "rw,noatime\n", "")

    monkeypatch.setattr("reforja.steps.storage.capture", fake_capture)

    assert step._is_mounted("/mnt/backup") is True
    # O findmnt recusa --target junto com --mountpoint ("impossivel combinar"), e
    # o rc=1 resultante fazia TODO ponto de montagem parecer desmontado.
    assert not {"--target", "--mountpoint"} <= set(visto["cmd"])


def test_fstab_never_removes_mountpoint_outside_mnt(tmp_path: Path) -> None:
    step = make_fstab_step(tmp_path)

    step._remove_mountpoints(["/home/tester", "/", "/mnt/antigo"])
    log = step.ctx.logger.path.read_text(encoding="utf-8")

    assert "rmdir /mnt/antigo" in log
    assert "/home/tester" not in log
    assert "umount /\n" not in log


def test_fstab_apply_writes_block_with_selected_disks(tmp_path: Path, monkeypatch) -> None:
    step = make_fstab_step(tmp_path)
    # Marca o BACKUP e o SSD externo; nenhuma troca de mountpoint.
    calls = iter([[1, 2], []])
    monkeypatch.setattr("reforja.steps.storage.select_many", lambda *a, **k: next(calls))
    written: dict[str, str] = {}
    monkeypatch.setattr(
        "reforja.steps.storage.write_text_sudo",
        lambda path, content, runner, **k: written.update({"content": content}),
    )

    step.apply()

    content = written["content"]
    assert "UUID=bkp-uuid /mnt/backup ntfs" in content
    assert "UUID=ext-uuid /mnt/ssd-externo ext4" in content
    assert "x-systemd.automount" in content
    assert "UUID=win-uuid" not in content  # nao foi marcado
    assert "UUID=root-uuid / btrfs" in content  # o resto do fstab e preservado
    assert step.result.compliance == "aplicado"


def test_fstab_apply_respects_edited_mountpoint(tmp_path: Path, monkeypatch) -> None:
    step = make_fstab_step(tmp_path)
    calls = iter([[2], [0]])  # marca o SSD externo; depois pede pra trocar o caminho dele
    monkeypatch.setattr("reforja.steps.storage.select_many", lambda *a, **k: next(calls))
    monkeypatch.setattr("reforja.steps.storage.prompt_user", lambda *a, **k: "/mnt/jogos-externos")
    written: dict[str, str] = {}
    monkeypatch.setattr(
        "reforja.steps.storage.write_text_sudo",
        lambda path, content, runner, **k: written.update({"content": content}),
    )

    step.apply()

    assert "UUID=ext-uuid /mnt/jogos-externos ext4" in written["content"]


def test_fstab_apply_rejects_mountpoint_outside_mnt(tmp_path: Path, monkeypatch) -> None:
    step = make_fstab_step(tmp_path)
    calls = iter([[2], [0]])
    monkeypatch.setattr("reforja.steps.storage.select_many", lambda *a, **k: next(calls))
    monkeypatch.setattr("reforja.steps.storage.prompt_user", lambda *a, **k: "/home/tester/disco")
    written: dict[str, str] = {}
    monkeypatch.setattr(
        "reforja.steps.storage.write_text_sudo",
        lambda path, content, runner, **k: written.update({"content": content}),
    )

    step.apply()

    # Caminho invalido -> mantem o sugerido em vez de escrever dentro da /home.
    assert "UUID=ext-uuid /mnt/ssd-externo ext4" in written["content"]
    assert "/home/tester/disco" not in written["content"]


def test_fstab_apply_flags_attention_when_no_partition_found(tmp_path: Path) -> None:
    step = make_fstab_step(tmp_path, parts=[])

    step.apply()

    # Regressao do bug antigo: sondagem vazia nunca pode virar "bloco gravado".
    assert step.result.compliance == "atencao"
    assert step.result.status == "skipped"


def test_fstab_status_flags_empty_managed_block(tmp_path: Path) -> None:
    # Exatamente o estado em que o bug antigo deixava a maquina.
    fstab = FSTAB_BASE + "\n# BEGIN pos-formatacao-cachyos\n# END pos-formatacao-cachyos\n"
    step = make_fstab_step(tmp_path, fstab=fstab)

    step.status()

    assert step.result.compliance == "pendente"
    assert "nenhuma montagem no bloco do fstab" in step.result.missing_items


def test_fstab_undo_removes_block_and_keeps_rest(tmp_path: Path, monkeypatch) -> None:
    fstab = (
        FSTAB_BASE + "\n# BEGIN pos-formatacao-cachyos\n"
        "UUID=bkp-uuid /mnt/backup ntfs rw,nofail 0 0\n"
        "# END pos-formatacao-cachyos\n"
    )
    step = make_fstab_step(tmp_path, fstab=fstab)
    written: dict[str, str] = {}
    monkeypatch.setattr(
        "reforja.steps.storage.write_text_sudo",
        lambda path, content, runner, **k: written.update({"content": content}),
    )

    step.undo()

    assert "pos-formatacao-cachyos" not in written["content"]
    assert "UUID=bkp-uuid" not in written["content"]
    assert "UUID=root-uuid / btrfs" in written["content"]


def test_kde_apply_gestures_skips_machine_without_touchpad(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = KdeStep(ctx)

    monkeypatch.setattr("reforja.hardware.has_touchpad", lambda: False)
    monkeypatch.setattr(
        "reforja.steps.kde.install_system_or_aur",
        lambda *_args, **_kwargs: pytest.fail("nao deveria instalar sem touchpad"),
    )
    # Sem touchpad, a tarefa de gestos nem chega a ser oferecida: vem como indisponivel.
    gestos = {task.key: task for task in step.plan()}["gestos"]
    assert gestos.state == "indisponivel"
    assert "touchpad" in gestos.unavailable_reason

    # Aplicar a etapa nao instala nada de gestos: so o Num Lock, que e o unico item
    # aplicavel aqui. O motivo do descarte dos gestos fica registrado no log.
    step.apply()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert step.result.status == "done"
    assert "nenhum touchpad detectado" in log


def test_kde_status_gestures_not_applicable_without_touchpad(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = KdeStep(ctx)

    monkeypatch.setattr("reforja.hardware.has_touchpad", lambda: False)
    monkeypatch.setattr("reforja.steps.kde.os.environ", {"XDG_CURRENT_DESKTOP": "KDE"})

    step.status()

    # Gestos nao aplicaveis (sem touchpad) + Num Lock pendente => atencao geral.
    assert "sem touchpad" in step.result.summary
    assert step.result.compliance == "atencao"


def test_gpu_prime_probe_is_ok_on_single_gpu_desktop_without_prime_run(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = GpuStep(ctx)

    monkeypatch.setattr("reforja.steps.gaming.command_exists", lambda name: name != "prime-run")
    monkeypatch.setattr(step, "_gpu_count", lambda: 1)

    probe = step._probe_prime_gl()

    assert probe.status == "ok"
    assert "nao aplicavel" in probe.summary


def test_gpu_prime_probe_warns_on_hybrid_machine_without_prime_run(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = GpuStep(ctx)

    monkeypatch.setattr("reforja.steps.gaming.command_exists", lambda name: name != "prime-run")
    monkeypatch.setattr(step, "_gpu_count", lambda: 2)

    probe = step._probe_prime_gl()

    assert probe.status == "warn"


def test_choose_step_returns_selected_stage(tmp_path: Path, monkeypatch) -> None:
    logger = Logger(tmp_path, "test")

    monkeypatch.setattr("reforja.cli.clear_screen", lambda: None)
    monkeypatch.setattr("reforja.cli.choose_option", lambda *_args, **_kwargs: 1)

    selected = choose_step(logger)

    assert selected is ALL_STEPS[1]


def test_main_menu_runs_selected_bulk_action(tmp_path: Path, monkeypatch) -> None:
    logger = Logger(tmp_path, "test")
    # opcao 0 = Aplicar tudo; opcao 4 = Sair (menu plano de 5 itens)
    choices = iter([0, 4])
    called: list[str] = []

    monkeypatch.setattr("reforja.cli.clear_screen", lambda: None)
    monkeypatch.setattr("reforja.cli.choose_option", lambda *_args, **_kwargs: next(choices))
    monkeypatch.setattr("reforja.cli.run_all", lambda action, _logger: called.append(action))

    main_menu(logger)

    assert called == ["apply"]


def test_main_menu_opens_executar_etapas(tmp_path: Path, monkeypatch) -> None:
    logger = Logger(tmp_path, "test")
    # opcao 2 = Executar etapas...; depois opcao 4 = Sair
    choices = iter([2, 4])
    opened: list[bool] = []

    monkeypatch.setattr("reforja.cli.clear_screen", lambda: None)
    monkeypatch.setattr("reforja.cli.choose_option", lambda *_args, **_kwargs: next(choices))
    monkeypatch.setattr("reforja.cli.select_and_run", lambda _logger: opened.append(True))

    main_menu(logger)

    assert opened == [True]


def test_main_menu_instala_gui_do_reforja(tmp_path: Path, monkeypatch) -> None:
    logger = Logger(tmp_path, "test")
    # opcao 3 = Instalar GUI do Reforja no sistema; depois opcao 4 = Sair
    choices = iter([3, 4])
    installed: list[bool] = []

    monkeypatch.setattr("reforja.cli.clear_screen", lambda: None)
    monkeypatch.setattr("reforja.cli.choose_option", lambda *_args, **_kwargs: next(choices))
    monkeypatch.setattr("reforja.cli.install_reforja_gui", lambda _logger: installed.append(True))

    main_menu(logger)

    assert installed == [True]


def test_install_reforja_gui_dispara_passo_15_preselecionado(tmp_path: Path, monkeypatch) -> None:
    logger = Logger(tmp_path, "test")
    captured: dict = {}

    def fake_run_action_safe(step_cls, action, _logger, *, configure=None):
        step = step_cls(make_ctx(tmp_path))
        if configure is not None:
            configure(step)
        captured["id"] = step_cls.id
        captured["action"] = action
        captured["preselect"] = step.preselect_names
        return None

    monkeypatch.setattr("reforja.cli.run_action_safe", fake_run_action_safe)

    install_reforja_gui(logger)

    assert captured == {"id": "15", "action": "apply", "preselect": ("Reforja",)}


def test_appimages_preselecao_pula_menu_e_processa_so_reforja(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)  # dry-run
    step = UpdateAppImagesStep(ctx)
    step.preselect_names = ("Reforja",)

    def fail_select(*_args, **_kwargs):
        raise AssertionError("select_many nao deveria ser chamado com preselecao")

    monkeypatch.setattr("reforja.steps_base.select_many", fail_select)
    fake_fetch, fake_capture, _calls = _fake_github_release("v1.0.9", "Reforja-1.0.9-x86_64.AppImage")
    monkeypatch.setattr("reforja.steps.appimage.fetch_json", fake_fetch)
    monkeypatch.setattr("reforja.steps.appimage.capture", fake_capture)
    monkeypatch.setattr("reforja.steps.appimage.copy_asset", lambda *_a, **_k: None)
    monkeypatch.setattr("reforja.steps.appimage.install_desktop_entry", lambda *_a, **_k: None)

    step.apply()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert "Reforja: nao instalado; instalando." in log
    assert "Hydra" not in log  # nao preselecionado: nem processado, nem listado


def test_select_and_run_runs_only_chosen_steps(tmp_path: Path, monkeypatch) -> None:
    logger = Logger(tmp_path, "test")
    ran: list[tuple[list[type], str]] = []

    monkeypatch.setattr("reforja.cli.clear_screen", lambda: None)
    # marca apenas a etapa 15 (Atualizar AppImages) pela posicao na lista
    target_index = next(i for i, s in enumerate(ALL_STEPS) if s.id == "15")
    monkeypatch.setattr("reforja.cli.choose_multiple", lambda **_kwargs: [target_index])
    monkeypatch.setattr("reforja.cli.choose_action", lambda *_a, **_k: "apply")
    # A tela de selecao de itens sonda a maquina real; nos testes ela e neutralizada.
    monkeypatch.setattr("reforja.cli.plan_selection", lambda *_a, **_k: {})
    monkeypatch.setattr("reforja.cli.run_steps", lambda steps, action, _logger, **_k: ran.append((steps, action)))

    select_and_run(logger)

    assert len(ran) == 1
    steps, action = ran[0]
    assert [s.id for s in steps] == ["15"]
    assert action == "apply"


def test_select_and_run_cancel_action_runs_nothing(tmp_path: Path, monkeypatch) -> None:
    logger = Logger(tmp_path, "test")
    ran: list[tuple] = []

    monkeypatch.setattr("reforja.cli.clear_screen", lambda: None)
    monkeypatch.setattr("reforja.cli.choose_multiple", lambda **_kwargs: [0])
    # Opcao "Cancelar" (indice 3) devolve None -> nada roda.
    monkeypatch.setattr("reforja.cli.choose_option", lambda **_kwargs: 3)
    monkeypatch.setattr("reforja.cli.run_steps", lambda *_a, **_k: ran.append(_a))

    select_and_run(logger)

    assert ran == []


def test_status_tones_cover_all_known_statuses() -> None:
    from reforja.cli import COMPLIANCE_TONES, STATUS_TONES

    assert set(STATUS_TONES) == {"done", "skipped", "manual", "failed", "blocked"}
    assert set(COMPLIANCE_TONES) == {"aplicado", "pendente", "atencao"}


def test_select_and_run_does_nothing_when_no_step_selected(tmp_path: Path, monkeypatch) -> None:
    logger = Logger(tmp_path, "test")
    ran: list[tuple] = []

    monkeypatch.setattr("reforja.cli.clear_screen", lambda: None)
    monkeypatch.setattr("reforja.cli.choose_multiple", lambda **_kwargs: [])  # nada marcado
    monkeypatch.setattr("reforja.cli.run_steps", lambda *_a, **_k: ran.append(_a))

    select_and_run(logger)

    assert ran == []  # nada foi executado
    assert "Nenhuma etapa marcada" in logger.path.read_text(encoding="utf-8")


def test_choose_multiple_fallback_empty_answer_selects_none(tmp_path: Path, monkeypatch) -> None:
    from reforja import tui

    logger = Logger(tmp_path, "test")
    monkeypatch.setattr("reforja.tui.prompt_user", lambda *_a, **_k: "")  # Enter sem digitar nada

    result = tui._choose_multiple_fallback(
        title="Executar etapas",
        logger=logger,
        prompt="Quais etapas",
        options=[MenuOption("1", "A"), MenuOption("2", "B")],
        footer=None,
        detail=None,
        prompt_label="Selecione",
    )

    assert result == []


def test_step_menu_runs_selected_action(tmp_path: Path, monkeypatch) -> None:
    # opcao 1 = Status; opcao 3 = Sair (menu da etapa sem Dry-run)
    logger = Logger(tmp_path, "test")
    choices = iter([1, 3])
    called: list[str] = []

    monkeypatch.setattr("reforja.cli.clear_screen", lambda: None)
    monkeypatch.setattr("reforja.cli.choose_option", lambda *_args, **_kwargs: next(choices))
    monkeypatch.setattr("reforja.cli.run_action_safe", lambda _step_cls, action, _logger: called.append(action))

    step_menu(ALL_STEPS[0], logger)

    assert called == ["status"]


def test_run_all_clears_screen_before_first_step(tmp_path: Path, monkeypatch) -> None:
    logger = Logger(tmp_path, "test")
    call_order: list[str] = []

    monkeypatch.setattr("reforja.cli.clear_screen", lambda: call_order.append("clear"))
    monkeypatch.setattr(
        "reforja.cli.run_action",
        lambda *_args, **_kwargs: (
            call_order.append("run") or StepRunResult("00", "Preparar", "done", "ok", "aplicado", 0.1)
        ),
    )
    monkeypatch.setattr("reforja.cli.render_run_summary", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("reforja.cli.prompt_return_to_menu", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("reforja.cli.plan_selection", lambda *_a, **_k: {})

    run_all("apply", logger)

    assert call_order[0] == "clear"
    assert "run" in call_order


def test_hardware_list_gpus_parses_lspci_lines() -> None:
    lspci = (
        "00:02.0 VGA compatible controller: Intel UHD Graphics 630\n"
        "01:00.0 3D controller: NVIDIA Corporation GA107M [GeForce RTX 3050]\n"
        "00:1f.3 Audio device: Intel Cannon Lake PCH cAVS\n"
    )

    gpus = hardware.list_gpus(lspci)

    assert gpus == ["Intel UHD Graphics 630", "NVIDIA Corporation GA107M [GeForce RTX 3050]"]


def test_hardware_nvidia_gpu_name_extracts_from_smi() -> None:
    smi = "| 0  NVIDIA GeForce RTX 3050  Off | 00000000:01:00.0 |\n"

    assert hardware.nvidia_gpu_name(smi) == "0 NVIDIA GeForce RTX 3050 Off | 00000000:01:00.0"
    assert hardware.nvidia_gpu_name("nada aqui") is None


def test_hardware_gpu_vendors_classifies_amd_nvidia_intel() -> None:
    assert hardware.gpu_vendors(["Advanced Micro Devices, Inc. [AMD/ATI] Navi 48 [Radeon RX 9070 XT]"]) == {"amd"}
    assert hardware.gpu_vendors(["NVIDIA Corporation GA107M [GeForce RTX 3050]"]) == {"nvidia"}
    assert hardware.gpu_vendors(["Intel UHD Graphics 630"]) == {"intel"}
    assert hardware.gpu_vendors(["Intel UHD Graphics 630", "NVIDIA Corporation GA107M [GeForce RTX 3050]"]) == {
        "intel",
        "nvidia",
    }
    assert hardware.gpu_vendors([]) == set()


def test_hardware_amd_gpu_name_extracts_from_lspci() -> None:
    lspci = (
        "00:1f.3 Audio device: Intel Cannon Lake PCH cAVS\n"
        "0c:00.0 VGA compatible controller: Advanced Micro Devices, Inc. [AMD/ATI] Navi 48 [Radeon RX 9070 XT]\n"
    )
    assert hardware.amd_gpu_name(lspci) == "Advanced Micro Devices, Inc. [AMD/ATI] Navi 48 [Radeon RX 9070 XT]"
    assert hardware.amd_gpu_name("Intel UHD Graphics 630") is None


def test_gpu_apply_installs_amd_and_removes_nvidia_residue(tmp_path: Path, monkeypatch) -> None:
    from reforja.platform import Distro

    ctx = make_ctx(tmp_path)  # runner dry-run: confirm_phrase e pulado
    step = GpuStep(ctx)

    monkeypatch.setattr(
        "reforja.steps.gaming.current_distro",
        lambda: Distro(id="cachyos", id_like=("arch",), family="arch"),
    )
    monkeypatch.setattr("reforja.steps.gaming.command_exists", lambda name: True)
    monkeypatch.setattr("reforja.steps.gaming.os.environ", {"XDG_SESSION_TYPE": "wayland"})
    monkeypatch.setattr("reforja.hardware.amdgpu_active", lambda: True)
    monkeypatch.setattr("reforja.hardware.is_laptop", lambda: False)  # desktop de GPU unica: limpeza permitida
    monkeypatch.setattr("reforja.steps.gaming.system_installed", lambda pkg: True)
    monkeypatch.setattr("reforja.steps.gaming.flatpak_installed", lambda app_id: False)

    installed: list[str] = []
    removed: list[list[str]] = []
    cleaned: list[bool] = []
    monkeypatch.setattr("reforja.steps.gaming.install_system_package", lambda pkg, runner: installed.append(pkg))
    monkeypatch.setattr(
        "reforja.steps.gaming.installed_packages_matching",
        lambda needle: ["nvidia-utils", "linux-cachyos-nvidia-open"] if needle == "nvidia" else [],
    )
    monkeypatch.setattr(
        "reforja.steps.gaming.remove_system_packages",
        lambda pkgs, runner, **_kw: removed.append(list(pkgs)) or list(pkgs),
    )
    monkeypatch.setattr(step, "_clean_nvidia_system_files", lambda: cleaned.append(True))

    def fake_run(cmd, *_args, **_kwargs):
        if cmd == ["lspci"]:
            return CompletedProcess(
                cmd,
                0,
                stdout="0c:00.0 VGA compatible controller: Advanced Micro Devices, Inc. [AMD/ATI] "
                "Navi 48 [Radeon RX 9070 XT]\n",
            )
        if cmd == ["glxinfo", "-B"]:
            return CompletedProcess(
                cmd, 0, stdout="direct rendering: Yes\nOpenGL renderer string: AMD Radeon RX 9070 XT (radeonsi)\n"
            )
        if cmd == ["vulkaninfo", "--summary"]:
            return CompletedProcess(cmd, 0, stdout="driverName = radv\ndeviceName = AMD Radeon RX 9070 XT\n")
        raise AssertionError(cmd)

    monkeypatch.setattr(step, "_run_probe", fake_run)

    step.apply()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert "vulkan-radeon" in installed
    assert "lib32-vulkan-radeon" in installed
    assert removed == [["nvidia-utils", "linux-cachyos-nvidia-open"]]
    assert cleaned == [True]
    assert "Fabricante(s) de GPU detectado(s): AMD." in log
    assert "Vulkan AMD (RADV): driver Vulkan RADV ativo." in log
    assert step.result.status == "done"


def test_gpu_apply_on_hybrid_laptop_keeps_both_drivers(tmp_path: Path, monkeypatch) -> None:
    from reforja.platform import Distro

    ctx = make_ctx(tmp_path)
    step = GpuStep(ctx)

    monkeypatch.setattr(
        "reforja.steps.gaming.current_distro",
        lambda: Distro(id="cachyos", id_like=("arch",), family="arch"),
    )
    monkeypatch.setattr("reforja.steps.gaming.command_exists", lambda name: True)
    monkeypatch.setattr("reforja.steps.gaming.os.environ", {"XDG_SESSION_TYPE": "wayland"})
    monkeypatch.setattr("reforja.hardware.is_laptop", lambda: True)  # laptop hibrido: nunca remove driver
    monkeypatch.setattr("reforja.steps.gaming.system_installed", lambda pkg: True)
    monkeypatch.setattr("reforja.steps.gaming.flatpak_installed", lambda app_id: False)

    installed: list[str] = []
    removed: list[list[str]] = []
    cleaned: list[bool] = []
    monkeypatch.setattr("reforja.steps.gaming.install_system_package", lambda pkg, runner: installed.append(pkg))
    monkeypatch.setattr(
        "reforja.steps.gaming.installed_packages_matching",
        lambda needle: ["nvidia-utils"] if needle == "nvidia" else [],
    )
    monkeypatch.setattr(
        "reforja.steps.gaming.remove_system_packages",
        lambda pkgs, runner, **_kw: removed.append(list(pkgs)) or list(pkgs),
    )
    monkeypatch.setattr(step, "_clean_nvidia_system_files", lambda: cleaned.append(True))

    def fake_run(cmd, *_args, **_kwargs):
        if cmd == ["lspci"]:
            return CompletedProcess(
                cmd,
                0,
                stdout="00:02.0 VGA compatible controller: Intel UHD Graphics 630\n"
                "01:00.0 3D controller: NVIDIA Corporation GA107M [GeForce RTX 3050]\n",
            )
        if cmd == ["glxinfo", "-B"]:
            return CompletedProcess(
                cmd, 0, stdout="direct rendering: Yes\nOpenGL renderer string: Mesa Intel(R) Graphics\n"
            )
        if cmd == ["prime-run", "glxinfo", "-B"]:
            return CompletedProcess(
                cmd, 0, stdout="direct rendering: Yes\nOpenGL renderer string: NVIDIA GeForce RTX 3050\n"
            )
        if cmd == ["nvidia-smi"]:
            return CompletedProcess(cmd, 0, stdout="NVIDIA-SMI 610.43.02\n| 0 NVIDIA GeForce RTX 3050 |\n")
        raise AssertionError(cmd)

    monkeypatch.setattr(step, "_run_probe", fake_run)

    step.apply()
    log = ctx.logger.path.read_text(encoding="utf-8")

    # Instala o driver do fabricante presente (NVIDIA), mas NUNCA remove nada nem limpa initramfs.
    assert "nvidia-utils" in installed
    assert removed == []
    assert cleaned == []
    assert "Laptop/hibrido detectado" in log


def test_hardware_has_touchpad_reads_input_devices(monkeypatch) -> None:
    monkeypatch.setattr(hardware.Path, "read_text", lambda self, **_kw: 'N: Name="SynPS/2 Synaptics TouchPad"')
    assert hardware.has_touchpad() is True


def test_hardware_step_dry_run_does_not_write_report(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = HardwareStep(ctx)

    monkeypatch.setattr("reforja.steps.inventory.command_exists", lambda name: True)
    monkeypatch.setattr("reforja.hardware.command_exists", lambda name: True)

    step.apply()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert "Inventario de Hardware" in log
    assert not step.report_file.exists()
    assert step.result.status == "done"


def test_hardware_step_status_pending_without_report(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    step = HardwareStep(ctx)

    step.status()

    assert step.result.compliance == "pendente"


def test_hardware_step_status_applied_with_existing_report(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    step = HardwareStep(ctx)
    destino = step.report_file
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text("RELATORIO DE HARDWARE\n", encoding="utf-8")

    step.status()

    assert step.result.compliance == "aplicado"


def _antigravity_distro(family: str = "arch", *, immutable: bool = False):
    return type(
        "D",
        (),
        {
            "family": family,
            "immutable": immutable,
            "is_arch": family == "arch",
            "is_debian": family == "debian",
            "is_fedora": family == "fedora",
            "id": family,
        },
    )()


def _patch_antigravity_distro(monkeypatch, family: str = "arch", *, immutable: bool = False) -> None:
    monkeypatch.setattr("reforja.steps.dev.current_distro", lambda: _antigravity_distro(family, immutable=immutable))


def test_antigravity_status_pending_on_fresh_home(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = AntigravityStep(ctx)
    _patch_antigravity_distro(monkeypatch)
    monkeypatch.setattr(AntigravityStep, "_fetch_latest", lambda self: None)
    monkeypatch.setattr("reforja.steps.dev.os.environ", {"PATH": "/usr/bin"})

    step.status()

    assert step.result.compliance == "pendente"
    assert set(step.result.missing_items) == {"instalacao", "desktop", "wrapper"}


def test_antigravity_status_applied_when_everything_present(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = AntigravityStep(ctx)
    _patch_antigravity_distro(monkeypatch)
    monkeypatch.setattr(AntigravityStep, "_fetch_latest", lambda self: None)
    home = ctx.user.home
    (home / "Antigravity IDE").mkdir(parents=True)
    desktop = home / ".local/share/applications/antigravity-ide.desktop"
    desktop.parent.mkdir(parents=True)
    desktop.write_text("[Desktop Entry]\n", encoding="utf-8")
    wrapper = home / ".local/bin/antigravity-ide"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr("reforja.steps.dev.os.environ", {"PATH": str(home / ".local/bin")})

    step.status()

    assert step.result.compliance == "aplicado"


def test_antigravity_status_attention_when_path_missing(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = AntigravityStep(ctx)
    _patch_antigravity_distro(monkeypatch)
    monkeypatch.setattr(AntigravityStep, "_fetch_latest", lambda self: None)
    home = ctx.user.home
    wrapper = home / ".local/bin/antigravity-ide"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr("reforja.steps.dev.os.environ", {"PATH": "/usr/bin"})

    step.status()

    assert step.result.compliance == "atencao"


def test_antigravity_status_hints_new_version(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = AntigravityStep(ctx)
    _patch_antigravity_distro(monkeypatch)
    monkeypatch.setattr(AntigravityStep, "_fetch_latest", lambda self: {"name": "9.9.9", "url": "u", "sha256": None})
    home = ctx.user.home
    install_dir = home / "Antigravity IDE"
    install_dir.mkdir(parents=True)
    (install_dir / ".antigravity-version").write_text("2.0.6\n", encoding="utf-8")
    desktop = home / ".local/share/applications/antigravity-ide.desktop"
    desktop.parent.mkdir(parents=True)
    desktop.write_text("[Desktop Entry]\n", encoding="utf-8")
    wrapper = home / ".local/bin/antigravity-ide"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr("reforja.steps.dev.os.environ", {"PATH": str(home / ".local/bin")})

    step.status()

    assert any("9.9.9" in hint for hint in step.result.hints)


def test_antigravity_tarball_updates_when_marker_differs(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    ctx.runner.dry_run = False
    step = AntigravityStep(ctx)
    _patch_antigravity_distro(monkeypatch)
    monkeypatch.setattr(
        AntigravityStep,
        "_fetch_latest",
        lambda self: {"name": "9.9.9", "url": "https://example/Antigravity.tar.gz", "sha256": None},
    )
    monkeypatch.setattr("reforja.steps.dev.install_system_package", lambda *_a, **_k: None)
    monkeypatch.setattr("reforja.steps.dev.ensure_owner", lambda *_a, **_k: None)
    monkeypatch.setattr("reforja.steps.dev.backup_existing", lambda *_a, **_k: None)
    monkeypatch.setattr("reforja.steps.dev.os.environ", {"PATH": str(ctx.user.home / ".local/bin")})

    install_dir = ctx.user.home / "Antigravity IDE"
    install_dir.mkdir(parents=True)
    (install_dir / ".antigravity-version").write_text("1.0.0\n", encoding="utf-8")

    calls: list = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        if isinstance(cmd, list) and cmd[:2] == ["tar", "-xzf"]:
            dest = Path(cmd[cmd.index("-C") + 1])
            exe = dest / "antigravity" / "antigravity-ide"
            exe.parent.mkdir(parents=True, exist_ok=True)
            exe.write_text("#!/bin/sh\n", encoding="utf-8")
            exe.chmod(0o755)
        return CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(ctx.runner, "run", fake_run)

    step.apply()

    assert step.result.status == "done"
    assert (install_dir / ".antigravity-version").read_text(encoding="utf-8").strip() == "9.9.9"
    assert any(isinstance(c, list) and "https://example/Antigravity.tar.gz" in c for c in calls)


def test_antigravity_tarball_skips_when_up_to_date(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = AntigravityStep(ctx)
    _patch_antigravity_distro(monkeypatch)
    monkeypatch.setattr(AntigravityStep, "_fetch_latest", lambda self: {"name": "2.0.6", "url": "u", "sha256": None})
    monkeypatch.setattr("reforja.steps.dev.install_system_package", lambda *_a, **_k: None)
    home = ctx.user.home
    install_dir = home / "Antigravity IDE"
    install_dir.mkdir(parents=True)
    exe = install_dir / "antigravity-ide"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    exe.chmod(0o755)
    (install_dir / ".antigravity-version").write_text("2.0.6\n", encoding="utf-8")
    desktop = home / ".local/share/applications/antigravity-ide.desktop"
    desktop.parent.mkdir(parents=True)
    desktop.write_text(f"[Desktop Entry]\nExec={exe}\n", encoding="utf-8")
    wrapper = home / ".local/bin/antigravity-ide"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text(step._wrapper_content(exe), encoding="utf-8")
    monkeypatch.setattr("reforja.steps.dev.os.environ", {"PATH": str(home / ".local/bin")})

    step.apply()

    assert step.result.status == "skipped"


def test_antigravity_apply_native_uses_repo_and_package(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = AntigravityStep(ctx)
    _patch_antigravity_distro(monkeypatch, "debian")
    order: list[str] = []
    monkeypatch.setattr("reforja.steps.dev.ensure_antigravity_repo", lambda _r: order.append("repo"))

    def fake_run(cmd, **_kwargs):
        if isinstance(cmd, list) and "antigravity" in cmd:
            order.append("install")

    monkeypatch.setattr(ctx.runner, "run", fake_run)

    step.apply()

    assert order == ["repo", "install"]
    assert step.result.status == "done"


def test_antigravity_undo_removes_artifacts(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    ctx.runner.dry_run = False
    step = AntigravityStep(ctx)
    _patch_antigravity_distro(monkeypatch)
    home = ctx.user.home
    install_dir = home / "Antigravity IDE"
    install_dir.mkdir(parents=True)
    desktop = home / ".local/share/applications/antigravity-ide.desktop"
    desktop.parent.mkdir(parents=True)
    desktop.write_text("[Desktop Entry]\n", encoding="utf-8")
    wrapper = home / ".local/bin/antigravity-ide"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")

    step.undo()

    assert not install_dir.exists()
    assert not desktop.exists()
    assert not wrapper.exists()


# --------------------------------------------------------------------------- backup


def _select_all_backup(monkeypatch) -> None:
    monkeypatch.setattr(
        "reforja.steps.backup.select_many",
        lambda prompt, options, logger, *, detail=None, preselected=(): list(range(len(list(options)))),
    )


def _make_backup_step(tmp_path: Path, monkeypatch, *, flatpaks=(), encrypt=False):
    ctx = make_ctx(tmp_path)
    ctx.runner.dry_run = False
    monkeypatch.setattr("reforja.steps.backup.system_installed", lambda pkg: False)
    monkeypatch.setattr("reforja.steps.backup.flatpak_installed", lambda app_id: app_id in set(flatpaks))
    _select_all_backup(monkeypatch)
    monkeypatch.setattr("reforja.steps.backup.BackupStep._want_encryption", lambda self, sensitive: encrypt)
    return ctx, BackupStep(ctx)


def _write(path: Path, content: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_backup_manifest_picks_only_existing_paths_and_excludes(tmp_path: Path, monkeypatch) -> None:
    ctx, step = _make_backup_step(tmp_path, monkeypatch, flatpaks={"com.rtosta.zapzap"})
    home = ctx.user.home
    _write(home / ".gitconfig", "[user]\n")
    _write(home / ".config/heroic/games.json")
    _write(home / ".config/heroic/Cache/blob")
    _write(home / ".var/app/com.rtosta.zapzap/config/prefs")
    # Sunshine nao existe -> nao deve entrar.

    entries = step._present_entries()
    apps = {entry.app for entry, _paths in entries}
    relpaths, excludes = step._relpaths_and_excludes()

    assert {"Git", "Heroic (nativo)", "ZapZap"} <= apps
    assert "Sunshine" not in apps
    assert ".gitconfig" in relpaths
    assert ".config/heroic" in relpaths
    assert ".config/heroic/Cache" in excludes
    assert any(e.startswith(".var/app/com.rtosta.zapzap/") and e.endswith("/cache") for e in excludes)


def test_backup_manifest_stays_in_sync_with_apps_step(tmp_path: Path, monkeypatch) -> None:
    # Todo flatpak_id declarado na etapa 10 deve entrar no manifesto de backup.
    from reforja.steps.backup import _manifest_entries

    manifest_ids = {e.flatpak_id for e in _manifest_entries() if e.flatpak_id}
    step_ids = {d["flatpak_id"] for d in AppsStep.apps.values() if d["flatpak_id"]}
    assert step_ids <= manifest_ids


def test_backup_creates_archive_with_manifest_and_honors_excludes(tmp_path: Path, monkeypatch) -> None:
    import tarfile

    ctx, step = _make_backup_step(tmp_path, monkeypatch)
    home = ctx.user.home
    _write(home / ".gitconfig", "[user]\n\tname = tester\n")
    _write(home / ".config/heroic/games.json", "{}")
    _write(home / ".config/heroic/Cache/blob", "junk")

    step._do_backup()

    archives = list((home / "reforja-backups").glob("reforja-configs-*.tar.gz"))
    assert len(archives) == 1
    with tarfile.open(archives[0]) as tar:
        names = tar.getnames()
    assert ".gitconfig" in names
    assert ".config/heroic/games.json" in names
    assert "reforja-backup.json" in names
    assert ".config/heroic/Cache/blob" not in names
    assert step.result.status == "done"


def test_backup_skips_when_nothing_present(tmp_path: Path, monkeypatch) -> None:
    _ctx, step = _make_backup_step(tmp_path, monkeypatch)
    step._do_backup()
    assert step.result.status == "skipped"


def test_backup_prune_keeps_only_recent(tmp_path: Path, monkeypatch) -> None:
    import reforja.steps.backup as backup_mod

    monkeypatch.setattr(backup_mod, "KEEP_BACKUPS", 2)
    _ctx, step = _make_backup_step(tmp_path, monkeypatch)
    backup_dir = step._backup_dir()
    backup_dir.mkdir(parents=True)
    for i in range(5):
        archive = backup_dir / f"reforja-configs-2026010{i}-000000.tar.gz"
        archive.write_text("x", encoding="utf-8")
        os.utime(archive, (1000 + i, 1000 + i))

    step._do_prune()

    remaining = sorted(p.name for p in backup_dir.glob("reforja-configs-*.tar.gz"))
    assert remaining == [
        "reforja-configs-20260103-000000.tar.gz",
        "reforja-configs-20260104-000000.tar.gz",
    ]
    assert step.result.status == "done"


def test_restore_empty_path_skips(tmp_path: Path, monkeypatch) -> None:
    _ctx, step = _make_backup_step(tmp_path, monkeypatch)
    monkeypatch.setattr("reforja.steps.backup.prompt_user", lambda *a, **k: "")

    step._do_restore()

    assert step.result.status == "skipped"


def test_restore_round_trip(tmp_path: Path, monkeypatch) -> None:
    ctx, step = _make_backup_step(tmp_path, monkeypatch)
    home = ctx.user.home
    _write(home / ".gitconfig", "original\n")
    step._do_backup()
    archive = next((home / "reforja-backups").glob("reforja-configs-*.tar.gz"))

    (home / ".gitconfig").write_text("modificado depois\n", encoding="utf-8")
    monkeypatch.setattr("reforja.steps.backup.prompt_user", lambda *a, **k: str(archive))
    monkeypatch.setattr("reforja.steps.backup.confirm_phrase", lambda phrase, logger: True)

    step._do_restore()

    assert (home / ".gitconfig").read_text(encoding="utf-8") == "original\n"
    # O manifesto interno nunca deve vazar para o HOME.
    assert not (home / "reforja-backup.json").exists()
    assert step.result.status == "done"


def test_restore_only_selected_apps(tmp_path: Path, monkeypatch) -> None:
    ctx, step = _make_backup_step(tmp_path, monkeypatch)
    home = ctx.user.home
    _write(home / ".gitconfig", "git original\n")
    _write(home / ".config/sunshine/sunshine.conf", "sun original\n")
    step._do_backup()
    archive = next((home / "reforja-backups").glob("reforja-configs-*.tar.gz"))

    (home / ".gitconfig").write_text("git novo\n", encoding="utf-8")
    (home / ".config/sunshine/sunshine.conf").write_text("sun novo\n", encoding="utf-8")
    monkeypatch.setattr("reforja.steps.backup.prompt_user", lambda *a, **k: str(archive))
    monkeypatch.setattr("reforja.steps.backup.confirm_phrase", lambda phrase, logger: True)
    # Restaura so o app "Git".
    monkeypatch.setattr(step, "_choose_apps_to_restore", lambda apps: [a for a in apps if a["app"] == "Git"])

    step._do_restore()

    assert (home / ".gitconfig").read_text(encoding="utf-8") == "git original\n"
    assert (home / ".config/sunshine/sunshine.conf").read_text(encoding="utf-8") == "sun novo\n"


def test_restore_rejects_non_reforja_archive(tmp_path: Path, monkeypatch) -> None:
    import tarfile

    _ctx, step = _make_backup_step(tmp_path, monkeypatch)
    bogus = tmp_path / "aleatorio.tar.gz"
    payload = tmp_path / "algo.txt"
    payload.write_text("nada a ver\n", encoding="utf-8")
    with tarfile.open(bogus, "w:gz") as tar:
        tar.add(payload, arcname="algo.txt")
    monkeypatch.setattr("reforja.steps.backup.prompt_user", lambda *a, **k: str(bogus))
    confirmed = {"called": False}

    def _confirm(phrase, logger):
        confirmed["called"] = True
        return True

    monkeypatch.setattr("reforja.steps.backup.confirm_phrase", _confirm)

    step._do_restore()

    assert step.result.status == "manual"
    assert confirmed["called"] is False  # aborta antes de pedir confirmacao


def test_encryption_is_forced_when_sensitive_and_user_declines(tmp_path: Path, monkeypatch) -> None:
    if shutil.which("gpg") is None:
        pytest.skip("gpg nao disponivel")
    ctx = make_ctx(tmp_path)
    ctx.runner.dry_run = False
    monkeypatch.setattr("reforja.steps.backup.system_installed", lambda pkg: False)
    monkeypatch.setattr("reforja.steps.backup.flatpak_installed", lambda app_id: False)
    # Usuario DESMARCA a criptografia (select_many devolve vazio)...
    monkeypatch.setattr(
        "reforja.steps.backup.select_many",
        lambda prompt, options, logger, *, detail=None, preselected=(): [],
    )
    # ...e nao digita a frase de confirmacao para gravar sem cifrar.
    monkeypatch.setattr("reforja.steps.backup.confirm_phrase", lambda phrase, logger: False)
    step = BackupStep(ctx)

    # Com dado sensivel presente, a criptografia e mantida a forca.
    assert step._want_encryption(sensitive=True) is True
    # Sem dado sensivel, respeita a escolha de nao cifrar.
    assert step._want_encryption(sensitive=False) is False


def test_upload_runs_rclone_copy(tmp_path: Path, monkeypatch) -> None:
    ctx, step = _make_backup_step(tmp_path, monkeypatch)
    backup_dir = step._backup_dir()
    backup_dir.mkdir(parents=True)
    archive = backup_dir / "reforja-configs-20260101-000000.tar.gz"
    archive.write_text("x", encoding="utf-8")
    monkeypatch.setattr("reforja.steps.backup.command_exists", lambda name: True)
    monkeypatch.setattr("reforja.steps.backup.prompt_user", lambda *a, **k: "")
    recorded = []
    monkeypatch.setattr(ctx.runner, "run", lambda cmd, **k: recorded.append(cmd))

    step._do_upload()

    assert recorded and recorded[0][:2] == ["rclone", "copy"]
    assert str(archive) in recorded[0]
    assert recorded[0][-1] == "Google Drive:reforja-backups"
    assert step.result.status == "done"


def test_encrypted_backup_round_trip(tmp_path: Path, monkeypatch) -> None:
    if shutil.which("gpg") is None:
        pytest.skip("gpg nao disponivel")
    ctx, step = _make_backup_step(tmp_path, monkeypatch, encrypt=True)
    monkeypatch.setattr("reforja.steps.backup.BackupStep._read_passphrase", lambda self, *, confirm: "senha-secreta")
    home = ctx.user.home
    _write(home / ".gitconfig", "conteudo cifrado\n")

    step._do_backup()

    encrypted = list((home / "reforja-backups").glob("reforja-configs-*.tar.gz.gpg"))
    assert len(encrypted) == 1
    # Nenhum .tar.gz em claro deve sobrar no diretorio do Drive.
    assert not list((home / "reforja-backups").glob("reforja-configs-*.tar.gz"))
    assert step.result.status == "done"

    (home / ".gitconfig").write_text("mudou\n", encoding="utf-8")
    monkeypatch.setattr("reforja.steps.backup.prompt_user", lambda *a, **k: str(encrypted[0]))
    monkeypatch.setattr("reforja.steps.backup.confirm_phrase", lambda phrase, logger: True)

    step._do_restore()

    assert (home / ".gitconfig").read_text(encoding="utf-8") == "conteudo cifrado\n"
    assert step.result.status == "done"
