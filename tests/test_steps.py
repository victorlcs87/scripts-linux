import json
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from reforja import hardware
from reforja.cli import (
    choose_step,
    main_menu,
    render_run_summary,
    render_status_overview,
    run_all,
    select_and_run,
    step_menu,
)
from reforja.core import Logger, MenuOption, Runner, StepRunResult, UserInfo
from reforja.steps import (
    ALL_STEPS,
    AntigravityStep,
    AppsStep,
    FstabStep,
    GesturesStep,
    GitStep,
    GpuStep,
    HardwareStep,
    NumLockStep,
    ShellyStep,
    SunshineStep,
    UpdateAppImagesStep,
)
from reforja.steps_base import StepContext


@pytest.fixture(autouse=True)
def _select_all(monkeypatch):
    """Por padrao, o seletor multi-item (select_many) marca TODOS os itens, para
    preservar as assercoes de "instala tudo" dos testes existentes. Testes que
    querem uma selecao especifica podem sobrescrever o patch."""

    def _all(prompt, options, logger, *, detail=None):
        return list(range(len(list(options))))

    for module in ("gaming", "appimage", "browser"):
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
    step = NumLockStep(make_ctx(tmp_path))
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
    assert "helper AUR" not in log
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
    monkeypatch.setattr("reforja.steps.dev.select_many", lambda *a, **k: [])

    step.apply()

    assert step.result.status == "skipped"
    assert "Nenhuma acao selecionada" in step.result.message


def test_git_step_add_repo_clones_configures_author_and_records_state(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    runner = _RecordingRunner(ctx.logger)
    ctx.runner = runner
    step = GitStep(ctx)

    # So a acao "Adicionar repositorio" (indice 2) marcada.
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
    monkeypatch.setattr("reforja.steps.gaming.select_many", lambda *a, **k: [])
    step.apply()
    assert step.result.status == "skipped"
    assert "@openai/codex" not in ctx.logger.path.read_text(encoding="utf-8")


def test_apps_apply_respeita_selecao_parcial(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = AppsStep(ctx)
    # Seleciona apenas o "Codex CLI" (indice na ordem de step.apps).
    codex_index = list(step.apps).index("Codex CLI")
    monkeypatch.setattr("reforja.steps.gaming.select_many", lambda *a, **k: [codex_index])
    monkeypatch.setattr("reforja.steps.gaming.system_installed", lambda pkg: False)
    monkeypatch.setattr("reforja.steps.gaming.command_exists", lambda command: False)
    monkeypatch.setattr("reforja.steps.gaming.npm_global_installed", lambda pkg: False)

    step.apply()
    log = ctx.logger.path.read_text(encoding="utf-8")
    assert "@openai/codex" in log  # Codex foi processado
    assert "com.discordapp.Discord" not in log  # Discord (nao selecionado) foi ignorado
    assert step.result.applied_items == ["Codex CLI"]


def test_appimages_apply_nada_selecionado_pula(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = UpdateAppImagesStep(ctx)
    monkeypatch.setattr("reforja.steps.appimage.select_many", lambda *a, **k: [])
    step.apply()
    assert step.result.status == "skipped"


def test_apps_does_not_manage_hydra_or_bitwarden(tmp_path: Path) -> None:
    # Hydra migrou para o passo 15; Bitwarden ficou so no passo 03.
    step = AppsStep(make_ctx(tmp_path))
    assert "Hydra Launcher" not in step.apps
    assert "Bitwarden" not in step.apps


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
    assert "Hydra Launcher" in step.result.summary
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
    step = GesturesStep(ctx)
    helper = ctx.user.home / ".local/bin/kde-gnome-like-overview"

    rendered = step._libinput_config_content(helper)

    assert f"gesture swipe up 3 {helper}" in rendered
    assert f"gesture swipe down 3 {helper}" in rendered


def test_gestures_adds_user_to_input_group_when_missing(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = GesturesStep(ctx)
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
    step = GesturesStep(ctx)
    calls: list[list[str] | str] = []

    monkeypatch.setattr(step, "_user_in_group", lambda group: True)
    monkeypatch.setattr(ctx.runner, "run", lambda cmd, **_kwargs: calls.append(cmd))

    ready = step._ensure_input_group()

    assert ready is True
    assert calls == []


def test_gestures_reports_manual_command_when_input_group_add_fails(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = GesturesStep(ctx)

    monkeypatch.setattr(step, "_user_in_group", lambda group: False)
    monkeypatch.setattr(ctx.runner, "run", lambda cmd, **_kwargs: CompletedProcess(cmd, 1, stdout=""))

    ready = step._ensure_input_group()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert ready is False
    assert "sudo gpasswd -a tester input" in log


def test_gestures_status_reports_missing_package_cleanly(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = GesturesStep(ctx)

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
    step = GesturesStep(ctx)

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


def test_fstab_includes_backup_label_and_mountpoint(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    step = FstabStep(ctx)

    assert "BACKUP" in step.labels
    assert step._mountpoint("BACKUP") == "/mnt/backup"
    assert step._mountpoint("WINDOWS") == "/mnt/windows"


def test_fstab_build_lines_skips_missing_labels(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = FstabStep(ctx)
    devices = {"WINDOWS": "/dev/nvme1n1p3", "BACKUP": "/dev/sda1"}
    values = {
        ("/dev/nvme1n1p3", "UUID"): "1111-AAAA",
        ("/dev/nvme1n1p3", "TYPE"): "ntfs",
        ("/dev/sda1", "UUID"): "2222-BBBB",
        ("/dev/sda1", "TYPE"): "ntfs",
    }

    monkeypatch.setattr(step, "_blkid_label", lambda label: devices.get(label, ""))
    monkeypatch.setattr(step, "_blkid_value", lambda device, key: values[(device, key)])

    lines = step._build_lines()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert any("UUID=1111-AAAA /mnt/windows ntfs" in line for line in lines)
    assert any("UUID=2222-BBBB /mnt/backup ntfs" in line for line in lines)
    assert len(lines) == 2
    assert "Label nao encontrado: DADOS WINDOWS" in log
    assert "Label nao encontrado: JOGOS LINUX" in log


def test_gestures_apply_skips_machine_without_touchpad(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = GesturesStep(ctx)

    monkeypatch.setattr("reforja.hardware.has_touchpad", lambda: False)
    monkeypatch.setattr(
        "reforja.steps.kde.install_system_or_aur",
        lambda *_args, **_kwargs: pytest.fail("nao deveria instalar sem touchpad"),
    )

    step.apply()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert step.result.status == "skipped"
    assert step.result.compliance == "aplicado"
    assert "nenhum touchpad detectado" in log


def test_gestures_status_not_applicable_without_touchpad(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = GesturesStep(ctx)

    monkeypatch.setattr("reforja.hardware.has_touchpad", lambda: False)
    monkeypatch.setattr("reforja.steps.kde.os.environ", {"XDG_CURRENT_DESKTOP": "KDE"})

    step.status()

    assert step.result.compliance == "aplicado"
    assert "sem touchpad" in step.result.summary


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
    # opcao 0 = Aplicar tudo; opcao 3 = Sair (menu plano de 4 itens)
    choices = iter([0, 3])
    called: list[str] = []

    monkeypatch.setattr("reforja.cli.clear_screen", lambda: None)
    monkeypatch.setattr("reforja.cli.choose_option", lambda *_args, **_kwargs: next(choices))
    monkeypatch.setattr("reforja.cli.run_all", lambda action, _logger: called.append(action))

    main_menu(logger)

    assert called == ["apply"]


def test_main_menu_opens_executar_etapas(tmp_path: Path, monkeypatch) -> None:
    logger = Logger(tmp_path, "test")
    # opcao 2 = Executar etapas...; depois opcao 3 = Sair
    choices = iter([2, 3])
    opened: list[bool] = []

    monkeypatch.setattr("reforja.cli.clear_screen", lambda: None)
    monkeypatch.setattr("reforja.cli.choose_option", lambda *_args, **_kwargs: next(choices))
    monkeypatch.setattr("reforja.cli.select_and_run", lambda _logger: opened.append(True))

    main_menu(logger)

    assert opened == [True]


def test_select_and_run_runs_only_chosen_steps(tmp_path: Path, monkeypatch) -> None:
    logger = Logger(tmp_path, "test")
    ran: list[tuple[list[type], str]] = []

    monkeypatch.setattr("reforja.cli.clear_screen", lambda: None)
    # marca apenas a etapa 15 (Atualizar AppImages) pela posicao na lista
    target_index = next(i for i, s in enumerate(ALL_STEPS) if s.id == "15")
    monkeypatch.setattr("reforja.cli.choose_multiple", lambda **_kwargs: [target_index])
    monkeypatch.setattr("reforja.cli.choose_action", lambda *_a, **_k: "apply")
    monkeypatch.setattr("reforja.cli.run_steps", lambda steps, action, _logger: ran.append((steps, action)))

    select_and_run(logger)

    assert len(ran) == 1
    steps, action = ran[0]
    assert [s.id for s in steps] == ["15"]
    assert action == "apply"


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
