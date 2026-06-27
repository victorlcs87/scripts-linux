from pathlib import Path
from subprocess import CompletedProcess

import pytest

from postformat.cli import choose_step, main_menu, render_run_summary, render_status_overview, run_all, step_menu
from postformat.core import Logger, PromptInterruptedError, Runner, StepRunResult, UserInfo
from postformat import hardware
from postformat.steps import ALL_STEPS, AppsStep, FstabStep, GesturesStep, GitStep, HardwareStep, NvidiaSteamStep, NumLockStep, ShellyStep, SunshineStep, UpdateAppImagesStep
from postformat.steps_base import StepContext


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


def test_all_steps_use_sequential_ids() -> None:
    ids = [step.id for step in ALL_STEPS]

    assert ids == [f"{index:02d}" for index in range(len(ALL_STEPS))]
    assert all("." not in step_id for step_id in ids)


def test_sunshine_dry_run_mentions_udev_autostart_ufw_and_desktop(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = SunshineStep(ctx)

    monkeypatch.setattr("postformat.steps.install_system_package", lambda pkg, runner: runner.logger.write(f"instalaria {pkg}"))
    monkeypatch.setattr("postformat.steps.command_exists", lambda name: name in {"sunshine", "ufw", "update-desktop-database"})
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

    monkeypatch.setattr("postformat.steps.command_exists", lambda name: False)
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

    monkeypatch.setattr("postformat.steps.system_installed", lambda pkg: pkg == "sunshine")
    monkeypatch.setattr("postformat.steps.command_exists", lambda name: name in {"sunshine", "ufw", "ss"})
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

    monkeypatch.setattr("postformat.steps.system_installed", lambda pkg: pkg == "sunshine")
    monkeypatch.setattr("postformat.steps.command_exists", lambda name: name in {"sunshine", "ufw", "ss"})
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

    monkeypatch.setattr("postformat.steps.command_exists", lambda name: name == "ufw")

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

    monkeypatch.setattr("postformat.steps.command_exists", lambda name: name in {"flatpak", "shelly"})
    monkeypatch.setattr("postformat.steps.aur_helper", lambda: "paru")
    monkeypatch.setattr("postformat.steps.current_distro", lambda: type("Distro", (), {"is_arch": True, "is_debian": False, "is_fedora": False, "immutable": False, "id": "cachyos", "family": "arch"})())
    monkeypatch.setattr("postformat.steps.system_installed", lambda pkg: pkg == "fuse2")
    monkeypatch.setattr("postformat.steps.install_first_available", lambda *_args, **_kwargs: None)

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

    monkeypatch.setattr("postformat.steps.current_distro", lambda: type("Distro", (), {"is_arch": False, "is_debian": True, "is_fedora": False, "immutable": False, "id": "ubuntu", "family": "debian"})())
    monkeypatch.setattr("postformat.steps.command_exists", lambda name: name == "flatpak")
    monkeypatch.setattr("postformat.steps.system_installed", lambda pkg: pkg == "libfuse2")
    monkeypatch.setattr("postformat.steps.install_first_available", lambda *_args, **_kwargs: None)

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


def test_apps_dry_run_mentions_appimage_and_codex(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = AppsStep(ctx)

    monkeypatch.setattr("postformat.steps.current_distro", lambda: type("Distro", (), {"is_arch": True, "is_debian": False, "is_fedora": False, "immutable": False, "id": "cachyos", "family": "arch"})())
    monkeypatch.setattr("postformat.steps.system_installed", lambda pkg: False)
    monkeypatch.setattr("postformat.steps.system_package_exists", lambda pkg: pkg in {"steam", "heroic-games-launcher", "zapzap", "fuse2"})
    monkeypatch.setattr("postformat.steps.aur_helper", lambda: None)
    monkeypatch.setattr("postformat.steps.command_exists", lambda command: False)
    monkeypatch.setattr("postformat.steps.npm_global_installed", lambda pkg: False)

    step.apply()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert "fuse2" in log
    assert "@openai/codex" in log
    assert "com.discordapp.Discord" in log


def test_apps_on_debian_use_flatpak_for_heroic_and_zapzap_when_system_package_is_missing(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = AppsStep(ctx)
    installed_flatpaks: list[str] = []

    monkeypatch.setattr("postformat.steps.current_distro", lambda: type("Distro", (), {"is_arch": False, "is_debian": True, "is_fedora": False, "immutable": False, "id": "ubuntu", "family": "debian"})())
    monkeypatch.setattr("postformat.steps.install_system_or_aur", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("postformat.steps.install_flatpak", lambda app_id, _runner: installed_flatpaks.append(app_id))

    step._install_system_or_flatpak("heroic-games-launcher", "heroic-games-launcher-bin", "com.heroicgameslauncher.hgl")
    step._install_system_or_flatpak("zapzap", "zapzap", "com.rtosta.zapzap")

    assert installed_flatpaks == ["com.heroicgameslauncher.hgl", "com.rtosta.zapzap"]


def test_apps_on_immutable_fall_back_to_flatpak(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = AppsStep(ctx)
    installed_flatpaks: list[str] = []

    monkeypatch.setattr("postformat.steps.current_distro", lambda: type("Distro", (), {"is_arch": True, "is_debian": False, "is_fedora": False, "immutable": True, "id": "steamos", "family": "arch"})())
    monkeypatch.setattr("postformat.steps.install_system_or_aur", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("postformat.steps.install_flatpak", lambda app_id, _runner: installed_flatpaks.append(app_id))

    step._install_system_or_flatpak("heroic-games-launcher", "heroic-games-launcher-bin", "com.heroicgameslauncher.hgl")

    assert installed_flatpaks == ["com.heroicgameslauncher.hgl"]


def test_install_steam_enables_rpmfusion_on_mutable_fedora(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = AppsStep(ctx)
    rpmfusion_called: list[bool] = []

    monkeypatch.setattr("postformat.steps.current_distro", lambda: type("Distro", (), {"is_arch": False, "is_debian": False, "is_fedora": True, "immutable": False, "id": "fedora", "family": "fedora"})())
    monkeypatch.setattr("postformat.steps.ensure_rpmfusion", lambda _runner: rpmfusion_called.append(True))
    monkeypatch.setattr("postformat.steps.install_system_or_aur", lambda *_args, **_kwargs: True)
    monkeypatch.setattr("postformat.steps.command_exists", lambda command: False)
    monkeypatch.setattr("postformat.steps.system_installed", lambda pkg: False)

    step._install_steam()

    assert rpmfusion_called == [True]


def test_install_steam_skips_when_preinstalled_on_immutable(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = AppsStep(ctx)
    flatpaks: list[str] = []

    monkeypatch.setattr("postformat.steps.current_distro", lambda: type("Distro", (), {"is_arch": True, "is_debian": False, "is_fedora": False, "immutable": True, "id": "steamos", "family": "arch"})())
    monkeypatch.setattr("postformat.steps.command_exists", lambda command: command == "steam")
    monkeypatch.setattr("postformat.steps.system_installed", lambda pkg: False)
    monkeypatch.setattr("postformat.steps.install_flatpak", lambda app_id, _runner: flatpaks.append(app_id))

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


def test_git_step_ctrl_c_on_repo_url_becomes_skipped(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    ctx.runner.dry_run = False
    step = GitStep(ctx)

    monkeypatch.setattr("postformat.steps.install_system_package", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "postformat.steps.prompt_user",
        lambda *args, **kwargs: (_ for _ in ()).throw(PromptInterruptedError("entrada interrompida pelo usuario: Informe a URL")),
    )

    step.apply()

    assert step.result.status == "skipped"
    assert "cancelada pelo usuario" in step.result.message


def test_render_status_overview_groups_applied_pending_attention(tmp_path: Path) -> None:
    logger = Logger(tmp_path, "test")
    results = [
        StepRunResult("00", "Ecossistema", "done", "Flatpak, flathub, AUR helper e fuse2 estao prontos.", "aplicado", 0.1),
        StepRunResult("07", "Rclone", "manual", "Remote do Google Drive ainda nao foi configurado.", "pendente", 0.1),
        StepRunResult("12", "Antigravity", "done", "Antigravity esta instalado, mas ~/.local/bin ainda nao esta no PATH.", "atencao", 0.1),
    ]

    render_status_overview(logger, results, 13, 1.2)
    log = logger.path.read_text(encoding="utf-8")

    assert "Resumo inteligente do status" in log
    assert "[aplicado] 1" in log
    assert "[pendente] 1" in log
    assert "[atencao] 1" in log


def test_gpu_status_renders_friendly_summary_when_everything_is_ok(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = NvidiaSteamStep(ctx)

    monkeypatch.setattr("postformat.steps.command_exists", lambda name: True)
    monkeypatch.setattr("postformat.steps.os.environ", {"XDG_SESSION_TYPE": "wayland"})

    def fake_run(cmd, *_args, **_kwargs):
        if cmd == ["glxinfo", "-B"]:
            return CompletedProcess(cmd, 0, stdout="direct rendering: Yes\nOpenGL renderer string: Mesa Intel(R) Graphics\n")
        if cmd == ["prime-run", "glxinfo", "-B"]:
            return CompletedProcess(cmd, 0, stdout="direct rendering: Yes\nOpenGL renderer string: NVIDIA GeForce RTX 5050 Laptop GPU/PCIe/SSE2\n")
        if cmd == ["nvidia-smi"]:
            return CompletedProcess(cmd, 0, stdout="NVIDIA-SMI 610.43.02\n| 0 NVIDIA GeForce RTX 5050 Laptop GPU |\n")
        if cmd == ["pacman", "-Q", "steam", "heroic-games-launcher"]:
            return CompletedProcess(cmd, 0, stdout="steam 1.0.0.85-7\nheroic-games-launcher-bin 2.22.0-1\n")
        raise AssertionError(cmd)

    monkeypatch.setattr(step, "_run_probe", fake_run)
    monkeypatch.setattr("postformat.steps.system_installed", lambda pkg: pkg in {"steam", "heroic-games-launcher"})
    monkeypatch.setattr("postformat.steps.flatpak_installed", lambda app_id: False)

    step.status()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert "Sessao grafica: wayland detectado." in log
    assert "OpenGL (GPU primaria): renderer detectado: Mesa Intel(R) Graphics." in log
    assert "GPU NVIDIA dedicada: renderer detectado: NVIDIA GeForce RTX 5050 Laptop GPU/PCIe/SSE2." in log
    assert "Driver NVIDIA: nvidia-smi respondeu corretamente" in log
    assert "Tudo certo com sessao grafica, GPUs e launchers avaliados." in log
    assert "direct rendering: Yes" not in log


def test_gpu_status_marks_missing_heroic_as_warning_only(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = NvidiaSteamStep(ctx)

    monkeypatch.setattr("postformat.steps.command_exists", lambda name: True)
    monkeypatch.setattr("postformat.steps.os.environ", {"XDG_SESSION_TYPE": "x11"})

    def fake_run(cmd, *_args, **_kwargs):
        if cmd == ["glxinfo", "-B"]:
            return CompletedProcess(cmd, 0, stdout="direct rendering: Yes\nOpenGL renderer string: Mesa Intel(R) Graphics\n")
        if cmd == ["prime-run", "glxinfo", "-B"]:
            return CompletedProcess(cmd, 0, stdout="direct rendering: Yes\nOpenGL renderer string: NVIDIA GeForce RTX 5050 Laptop GPU/PCIe/SSE2\n")
        if cmd == ["nvidia-smi"]:
            return CompletedProcess(cmd, 0, stdout="NVIDIA-SMI 610.43.02\n| 0 NVIDIA GeForce RTX 5050 Laptop GPU |\n")
        if cmd == ["pacman", "-Q", "steam", "heroic-games-launcher"]:
            return CompletedProcess(cmd, 1, stdout="steam 1.0.0.85-7\nerror: package 'heroic-games-launcher' was not found\n")
        raise AssertionError(cmd)

    monkeypatch.setattr(step, "_run_probe", fake_run)
    monkeypatch.setattr("postformat.steps.system_installed", lambda pkg: pkg == "steam")
    monkeypatch.setattr("postformat.steps.flatpak_installed", lambda app_id: False)

    step.status()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert "Steam: instalado." in log
    assert "Heroic: ausente." in log
    assert "Validacao parcialmente pronta" in log
    assert "Problema(s) detectado(s)" not in log


def test_gpu_status_shows_short_details_when_prime_run_fails(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = NvidiaSteamStep(ctx)

    monkeypatch.setattr("postformat.steps.command_exists", lambda name: True)
    monkeypatch.setattr("postformat.steps.os.environ", {"XDG_SESSION_TYPE": "wayland"})

    def fake_run(cmd, *_args, **_kwargs):
        if cmd == ["glxinfo", "-B"]:
            return CompletedProcess(cmd, 0, stdout="direct rendering: Yes\nOpenGL renderer string: Mesa Intel(R) Graphics\n")
        if cmd == ["prime-run", "glxinfo", "-B"]:
            return CompletedProcess(cmd, 1, stdout="prime-run failed to start discrete GPU backend\n")
        if cmd == ["nvidia-smi"]:
            return CompletedProcess(cmd, 0, stdout="NVIDIA-SMI 610.43.02\n| 0 NVIDIA GeForce RTX 5050 Laptop GPU |\n")
        if cmd == ["pacman", "-Q", "steam", "heroic-games-launcher"]:
            return CompletedProcess(cmd, 0, stdout="steam 1.0.0.85-7\nheroic-games-launcher-bin 2.22.0-1\n")
        raise AssertionError(cmd)

    monkeypatch.setattr(step, "_run_probe", fake_run)
    monkeypatch.setattr("postformat.steps.system_installed", lambda pkg: pkg in {"steam", "heroic-games-launcher"})
    monkeypatch.setattr("postformat.steps.flatpak_installed", lambda app_id: False)

    step.status()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert "GPU NVIDIA dedicada: prime-run nao confirmou uso da GPU NVIDIA." in log
    assert "Detalhes: prime-run failed to start discrete GPU backend" in log
    assert "Problema(s) detectado(s): 1 item(ns) exigem revisao." in log


def test_gpu_status_flags_missing_direct_rendering(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = NvidiaSteamStep(ctx)

    monkeypatch.setattr("postformat.steps.command_exists", lambda name: True)
    monkeypatch.setattr("postformat.steps.os.environ", {"XDG_SESSION_TYPE": "wayland"})

    def fake_run(cmd, *_args, **_kwargs):
        if cmd == ["glxinfo", "-B"]:
            return CompletedProcess(cmd, 0, stdout="direct rendering: No\nOpenGL renderer string: Mesa Intel(R) Graphics\n")
        if cmd == ["prime-run", "glxinfo", "-B"]:
            return CompletedProcess(cmd, 0, stdout="direct rendering: Yes\nOpenGL renderer string: NVIDIA GeForce RTX 5050 Laptop GPU/PCIe/SSE2\n")
        if cmd == ["nvidia-smi"]:
            return CompletedProcess(cmd, 0, stdout="NVIDIA-SMI 610.43.02\n| 0 NVIDIA GeForce RTX 5050 Laptop GPU |\n")
        if cmd == ["pacman", "-Q", "steam", "heroic-games-launcher"]:
            return CompletedProcess(cmd, 0, stdout="steam 1.0.0.85-7\nheroic-games-launcher-bin 2.22.0-1\n")
        raise AssertionError(cmd)

    monkeypatch.setattr(step, "_run_probe", fake_run)
    monkeypatch.setattr("postformat.steps.system_installed", lambda pkg: pkg in {"steam", "heroic-games-launcher"})
    monkeypatch.setattr("postformat.steps.flatpak_installed", lambda app_id: False)

    step.status()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert "OpenGL (GPU primaria): OpenGL basico nao respondeu como esperado." in log
    assert "Detalhes: direct rendering: No OpenGL renderer string: Mesa Intel(R) Graphics" in log


def test_gpu_status_marks_missing_nvidia_smi_as_problem(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = NvidiaSteamStep(ctx)

    def fake_exists(name: str) -> bool:
        return name != "nvidia-smi"

    monkeypatch.setattr("postformat.steps.command_exists", fake_exists)
    monkeypatch.setattr("postformat.steps.os.environ", {"XDG_SESSION_TYPE": "wayland"})

    def fake_run(cmd, *_args, **_kwargs):
        if cmd == ["glxinfo", "-B"]:
            return CompletedProcess(cmd, 0, stdout="direct rendering: Yes\nOpenGL renderer string: Mesa Intel(R) Graphics\n")
        if cmd == ["prime-run", "glxinfo", "-B"]:
            return CompletedProcess(cmd, 0, stdout="direct rendering: Yes\nOpenGL renderer string: NVIDIA GeForce RTX 5050 Laptop GPU/PCIe/SSE2\n")
        if cmd == ["pacman", "-Q", "steam", "heroic-games-launcher"]:
            return CompletedProcess(cmd, 0, stdout="steam 1.0.0.85-7\nheroic-games-launcher-bin 2.22.0-1\n")
        raise AssertionError(cmd)

    monkeypatch.setattr(step, "_run_probe", fake_run)
    monkeypatch.setattr("postformat.steps.system_installed", lambda pkg: pkg in {"steam", "heroic-games-launcher"})
    monkeypatch.setattr("postformat.steps.flatpak_installed", lambda app_id: False)

    step.status()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert "Driver NVIDIA: nvidia-smi nao esta disponivel." in log
    assert "Problema(s) detectado(s): 1 item(ns) exigem revisao." in log


def test_apps_detect_install_source_prefers_existing_flatpak(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = AppsStep(ctx)

    monkeypatch.setattr("postformat.steps.system_installed", lambda pkg: False)
    monkeypatch.setattr("postformat.steps.flatpak_installed", lambda app_id: app_id == "com.discordapp.Discord")
    monkeypatch.setattr("postformat.steps.command_exists", lambda command: command == "flatpak")
    monkeypatch.setattr("postformat.steps.npm_global_installed", lambda pkg: False)

    assert step._detect_install_source("Discord") == "flatpak (com.discordapp.Discord)"


def test_apps_detect_install_source_for_hydra_appimage(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = AppsStep(ctx)
    hydra = ctx.user.home / "AppImages/HydraLauncher-latest.AppImage"
    hydra.parent.mkdir(parents=True, exist_ok=True)
    hydra.write_text("bin", encoding="utf-8")

    monkeypatch.setattr("postformat.steps.system_installed", lambda pkg: False)
    monkeypatch.setattr("postformat.steps.flatpak_installed", lambda app_id: False)
    monkeypatch.setattr("postformat.steps.command_exists", lambda command: False)
    monkeypatch.setattr("postformat.steps.npm_global_installed", lambda pkg: False)

    source = step._detect_install_source("Hydra Launcher")

    assert source is not None
    assert source.startswith("appimage")


def test_apps_detect_install_source_for_hydra_canonical_desktop(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = AppsStep(ctx)
    desktop = ctx.user.home / ".local/share/applications/hydralauncher.desktop"
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text("[Desktop Entry]\nName=Hydra Launcher\n", encoding="utf-8")

    monkeypatch.setattr("postformat.steps.system_installed", lambda pkg: False)
    monkeypatch.setattr("postformat.steps.flatpak_installed", lambda app_id: False)
    monkeypatch.setattr("postformat.steps.command_exists", lambda command: False)
    monkeypatch.setattr("postformat.steps.npm_global_installed", lambda pkg: False)

    source = step._detect_install_source("Hydra Launcher")

    assert source == f"desktop ({desktop})"


def test_install_hydra_reconciles_desktop_when_appimage_exists(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = AppsStep(ctx)
    hydra = ctx.user.home / "AppImages/HydraLauncher-latest.AppImage"
    hydra.parent.mkdir(parents=True, exist_ok=True)
    hydra.write_text("bin", encoding="utf-8")
    copied_icons: list[Path] = []
    installed_desktops: list[tuple[Path, str]] = []

    monkeypatch.setattr("postformat.steps.copy_asset", lambda _source, target, _runner: copied_icons.append(target))
    monkeypatch.setattr(
        "postformat.steps.install_desktop_entry",
        lambda path, entry, _runner: installed_desktops.append((path, entry.render())),
    )
    monkeypatch.setattr("postformat.steps.install_system_package", lambda *_args, **_kwargs: pytest.fail("nao deveria instalar pacotes"))
    monkeypatch.setattr("postformat.steps.install_first_available", lambda *_args, **_kwargs: pytest.fail("nao deveria instalar fuse"))

    step._install_hydra()

    assert copied_icons == [ctx.user.home / ".local/share/icons/hydra-launcher.png"]
    assert installed_desktops
    desktop_path, rendered = installed_desktops[0]
    assert desktop_path == ctx.user.home / ".local/share/applications/hydralauncher.desktop"
    assert f"Exec={hydra} %U" in rendered
    assert "StartupWMClass=hydralauncher" in rendered


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
    monkeypatch.setattr("postformat.steps.copy_asset", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "postformat.steps.install_desktop_entry",
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

    monkeypatch.setattr("postformat.hardware.has_touchpad", lambda: True)
    monkeypatch.setattr("postformat.steps.system_installed", lambda pkg: False)
    monkeypatch.setattr("postformat.steps.command_exists", lambda command: False)
    monkeypatch.setattr("postformat.steps.os.environ", {"XDG_CURRENT_DESKTOP": "KDE"})

    step.status()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert "libinput-gestures" in log
    assert "ausente" in log


def test_gestures_status_marks_attention_when_group_or_service_are_missing(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = GesturesStep(ctx)

    monkeypatch.setattr("postformat.hardware.has_touchpad", lambda: True)
    monkeypatch.setattr("postformat.steps.system_installed", lambda pkg: pkg == "libinput-gestures")
    monkeypatch.setattr("postformat.steps.command_exists", lambda command: command == "libinput-gestures-setup")
    monkeypatch.setattr(step, "_user_in_group", lambda group: False)
    monkeypatch.setattr(step, "_libinput_gestures_running", lambda: False)
    monkeypatch.setattr("postformat.steps.os.environ", {"XDG_CURRENT_DESKTOP": "KDE"})

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

    monkeypatch.setattr("postformat.hardware.has_touchpad", lambda: False)
    monkeypatch.setattr("postformat.steps.install_system_or_aur", lambda *_args, **_kwargs: pytest.fail("nao deveria instalar sem touchpad"))

    step.apply()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert step.result.status == "skipped"
    assert step.result.compliance == "aplicado"
    assert "nenhum touchpad detectado" in log


def test_gestures_status_not_applicable_without_touchpad(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = GesturesStep(ctx)

    monkeypatch.setattr("postformat.hardware.has_touchpad", lambda: False)
    monkeypatch.setattr("postformat.steps.os.environ", {"XDG_CURRENT_DESKTOP": "KDE"})

    step.status()

    assert step.result.compliance == "aplicado"
    assert "sem touchpad" in step.result.summary


def test_gpu_prime_probe_is_ok_on_single_gpu_desktop_without_prime_run(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = NvidiaSteamStep(ctx)

    monkeypatch.setattr("postformat.steps.command_exists", lambda name: name != "prime-run")
    monkeypatch.setattr(step, "_gpu_count", lambda: 1)

    probe = step._probe_prime_gl()

    assert probe.status == "ok"
    assert "nao aplicavel" in probe.summary


def test_gpu_prime_probe_warns_on_hybrid_machine_without_prime_run(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = NvidiaSteamStep(ctx)

    monkeypatch.setattr("postformat.steps.command_exists", lambda name: name != "prime-run")
    monkeypatch.setattr(step, "_gpu_count", lambda: 2)

    probe = step._probe_prime_gl()

    assert probe.status == "warn"


def test_choose_step_returns_selected_stage(tmp_path: Path, monkeypatch) -> None:
    logger = Logger(tmp_path, "test")

    monkeypatch.setattr("postformat.cli.clear_screen", lambda: None)
    monkeypatch.setattr("postformat.cli.choose_option", lambda *_args, **_kwargs: 1)

    selected = choose_step(logger)

    assert selected is ALL_STEPS[1]


def test_main_menu_runs_selected_bulk_action(tmp_path: Path, monkeypatch) -> None:
    logger = Logger(tmp_path, "test")
    choices = iter([0, 6])
    called: list[str] = []

    monkeypatch.setattr("postformat.cli.clear_screen", lambda: None)
    monkeypatch.setattr("postformat.cli.choose_option", lambda *_args, **_kwargs: next(choices))
    monkeypatch.setattr("postformat.cli.run_all", lambda action, _logger: called.append(action))

    main_menu(logger)

    assert called == ["apply"]


def test_step_menu_runs_selected_action(tmp_path: Path, monkeypatch) -> None:
    logger = Logger(tmp_path, "test")
    choices = iter([2, 4])
    called: list[str] = []

    monkeypatch.setattr("postformat.cli.clear_screen", lambda: None)
    monkeypatch.setattr("postformat.cli.choose_option", lambda *_args, **_kwargs: next(choices))
    monkeypatch.setattr("postformat.cli.run_action_safe", lambda _step_cls, action, _logger: called.append(action))

    step_menu(ALL_STEPS[0], logger)

    assert called == ["status"]


def test_run_all_clears_screen_before_first_step(tmp_path: Path, monkeypatch) -> None:
    logger = Logger(tmp_path, "test")
    call_order: list[str] = []

    monkeypatch.setattr("postformat.cli.clear_screen", lambda: call_order.append("clear"))
    monkeypatch.setattr("postformat.cli.run_action", lambda *_args, **_kwargs: call_order.append("run") or StepRunResult("00", "Preparar", "done", "ok", "aplicado", 0.1))
    monkeypatch.setattr("postformat.cli.render_run_summary", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("postformat.cli.prompt_return_to_menu", lambda *_args, **_kwargs: None)

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


def test_hardware_has_touchpad_reads_input_devices(monkeypatch) -> None:
    monkeypatch.setattr(hardware.Path, "read_text", lambda self, **_kw: "N: Name=\"SynPS/2 Synaptics TouchPad\"")
    assert hardware.has_touchpad() is True


def test_hardware_step_dry_run_does_not_write_report(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = HardwareStep(ctx)

    monkeypatch.setattr("postformat.steps.command_exists", lambda name: True)
    monkeypatch.setattr("postformat.hardware.command_exists", lambda name: True)

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
