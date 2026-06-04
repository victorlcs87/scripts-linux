from pathlib import Path
from subprocess import CompletedProcess

from postformat.cli import choose_step, main_menu, render_run_summary, render_status_overview, step_menu
from postformat.core import Logger, PromptInterruptedError, Runner, StepRunResult, UserInfo
from postformat.steps import ALL_STEPS, AppsStep, GesturesStep, GitStep, NvidiaSteamStep, NumLockStep, ShellyStep
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


def test_shelly_step_dry_run_prepares_stack_without_ui_when_ready(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = ShellyStep(ctx)

    monkeypatch.setattr("postformat.steps.command_exists", lambda name: name in {"flatpak", "shelly"})
    monkeypatch.setattr("postformat.steps.aur_helper", lambda: "paru")
    monkeypatch.setattr("postformat.steps.pacman_installed", lambda pkg: pkg == "fuse2")

    def fake_run(cmd, **kwargs):
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


def test_apps_dry_run_mentions_appimage_and_codex(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    step = AppsStep(ctx)

    step.apply()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert "fuse2" in log
    assert "@openai/codex" in log
    assert "com.discordapp.Discord" in log


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
    step = GitStep(ctx)

    monkeypatch.setattr("postformat.steps.install_pacman", lambda *args, **kwargs: None)
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

    def fake_run(cmd, **kwargs):
        if cmd == ["glxinfo", "-B"]:
            return CompletedProcess(cmd, 0, stdout="direct rendering: Yes\nOpenGL renderer string: Mesa Intel(R) Graphics\n")
        if cmd == ["prime-run", "glxinfo", "-B"]:
            return CompletedProcess(cmd, 0, stdout="direct rendering: Yes\nOpenGL renderer string: NVIDIA GeForce RTX 5050 Laptop GPU/PCIe/SSE2\n")
        if cmd == ["nvidia-smi"]:
            return CompletedProcess(cmd, 0, stdout="NVIDIA-SMI 610.43.02\n| 0 NVIDIA GeForce RTX 5050 Laptop GPU |\n")
        if cmd == ["pacman", "-Q", "steam", "heroic-games-launcher"]:
            return CompletedProcess(cmd, 0, stdout="steam 1.0.0.85-7\nheroic-games-launcher-bin 2.22.0-1\n")
        raise AssertionError(cmd)

    monkeypatch.setattr(ctx.runner, "run", fake_run)

    step.status()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert "Sessao grafica: wayland detectado." in log
    assert "GPU integrada / OpenGL: renderer detectado: Mesa Intel(R) Graphics." in log
    assert "GPU NVIDIA dedicada: renderer detectado: NVIDIA GeForce RTX 5050 Laptop GPU/PCIe/SSE2." in log
    assert "Driver NVIDIA: nvidia-smi respondeu corretamente" in log
    assert "Tudo certo com sessao grafica, GPUs e launchers avaliados." in log
    assert "direct rendering: Yes" not in log


def test_gpu_status_marks_missing_heroic_as_warning_only(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = NvidiaSteamStep(ctx)

    monkeypatch.setattr("postformat.steps.command_exists", lambda name: True)
    monkeypatch.setattr("postformat.steps.os.environ", {"XDG_SESSION_TYPE": "x11"})

    def fake_run(cmd, **kwargs):
        if cmd == ["glxinfo", "-B"]:
            return CompletedProcess(cmd, 0, stdout="direct rendering: Yes\nOpenGL renderer string: Mesa Intel(R) Graphics\n")
        if cmd == ["prime-run", "glxinfo", "-B"]:
            return CompletedProcess(cmd, 0, stdout="direct rendering: Yes\nOpenGL renderer string: NVIDIA GeForce RTX 5050 Laptop GPU/PCIe/SSE2\n")
        if cmd == ["nvidia-smi"]:
            return CompletedProcess(cmd, 0, stdout="NVIDIA-SMI 610.43.02\n| 0 NVIDIA GeForce RTX 5050 Laptop GPU |\n")
        if cmd == ["pacman", "-Q", "steam", "heroic-games-launcher"]:
            return CompletedProcess(cmd, 1, stdout="steam 1.0.0.85-7\nerror: package 'heroic-games-launcher' was not found\n")
        raise AssertionError(cmd)

    monkeypatch.setattr(ctx.runner, "run", fake_run)

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

    def fake_run(cmd, **kwargs):
        if cmd == ["glxinfo", "-B"]:
            return CompletedProcess(cmd, 0, stdout="direct rendering: Yes\nOpenGL renderer string: Mesa Intel(R) Graphics\n")
        if cmd == ["prime-run", "glxinfo", "-B"]:
            return CompletedProcess(cmd, 1, stdout="prime-run failed to start discrete GPU backend\n")
        if cmd == ["nvidia-smi"]:
            return CompletedProcess(cmd, 0, stdout="NVIDIA-SMI 610.43.02\n| 0 NVIDIA GeForce RTX 5050 Laptop GPU |\n")
        if cmd == ["pacman", "-Q", "steam", "heroic-games-launcher"]:
            return CompletedProcess(cmd, 0, stdout="steam 1.0.0.85-7\nheroic-games-launcher-bin 2.22.0-1\n")
        raise AssertionError(cmd)

    monkeypatch.setattr(ctx.runner, "run", fake_run)

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

    def fake_run(cmd, **kwargs):
        if cmd == ["glxinfo", "-B"]:
            return CompletedProcess(cmd, 0, stdout="direct rendering: No\nOpenGL renderer string: Mesa Intel(R) Graphics\n")
        if cmd == ["prime-run", "glxinfo", "-B"]:
            return CompletedProcess(cmd, 0, stdout="direct rendering: Yes\nOpenGL renderer string: NVIDIA GeForce RTX 5050 Laptop GPU/PCIe/SSE2\n")
        if cmd == ["nvidia-smi"]:
            return CompletedProcess(cmd, 0, stdout="NVIDIA-SMI 610.43.02\n| 0 NVIDIA GeForce RTX 5050 Laptop GPU |\n")
        if cmd == ["pacman", "-Q", "steam", "heroic-games-launcher"]:
            return CompletedProcess(cmd, 0, stdout="steam 1.0.0.85-7\nheroic-games-launcher-bin 2.22.0-1\n")
        raise AssertionError(cmd)

    monkeypatch.setattr(ctx.runner, "run", fake_run)

    step.status()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert "GPU integrada / OpenGL: OpenGL basico nao respondeu como esperado." in log
    assert "Detalhes: direct rendering: No OpenGL renderer string: Mesa Intel(R) Graphics" in log


def test_gpu_status_marks_missing_nvidia_smi_as_problem(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = NvidiaSteamStep(ctx)

    def fake_exists(name: str) -> bool:
        return name != "nvidia-smi"

    monkeypatch.setattr("postformat.steps.command_exists", fake_exists)
    monkeypatch.setattr("postformat.steps.os.environ", {"XDG_SESSION_TYPE": "wayland"})

    def fake_run(cmd, **kwargs):
        if cmd == ["glxinfo", "-B"]:
            return CompletedProcess(cmd, 0, stdout="direct rendering: Yes\nOpenGL renderer string: Mesa Intel(R) Graphics\n")
        if cmd == ["prime-run", "glxinfo", "-B"]:
            return CompletedProcess(cmd, 0, stdout="direct rendering: Yes\nOpenGL renderer string: NVIDIA GeForce RTX 5050 Laptop GPU/PCIe/SSE2\n")
        if cmd == ["pacman", "-Q", "steam", "heroic-games-launcher"]:
            return CompletedProcess(cmd, 0, stdout="steam 1.0.0.85-7\nheroic-games-launcher-bin 2.22.0-1\n")
        raise AssertionError(cmd)

    monkeypatch.setattr(ctx.runner, "run", fake_run)

    step.status()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert "Driver NVIDIA: nvidia-smi nao esta disponivel." in log
    assert "Problema(s) detectado(s): 1 item(ns) exigem revisao." in log


def test_apps_detect_install_source_prefers_existing_flatpak(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = AppsStep(ctx)

    monkeypatch.setattr("postformat.steps.pacman_installed", lambda pkg: False)
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

    monkeypatch.setattr("postformat.steps.pacman_installed", lambda pkg: False)
    monkeypatch.setattr("postformat.steps.flatpak_installed", lambda app_id: False)
    monkeypatch.setattr("postformat.steps.command_exists", lambda command: False)
    monkeypatch.setattr("postformat.steps.npm_global_installed", lambda pkg: False)

    source = step._detect_install_source("Hydra Launcher")

    assert source is not None
    assert source.startswith("appimage")


def test_gestures_status_reports_missing_package_cleanly(tmp_path: Path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    step = GesturesStep(ctx)

    monkeypatch.setattr("postformat.steps.pacman_installed", lambda pkg: False)
    monkeypatch.setattr("postformat.steps.command_exists", lambda command: False)
    monkeypatch.setattr("postformat.steps.os.environ", {"XDG_CURRENT_DESKTOP": "KDE"})

    step.status()
    log = ctx.logger.path.read_text(encoding="utf-8")

    assert "libinput-gestures" in log
    assert "ausente" in log


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
