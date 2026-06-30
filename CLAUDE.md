# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Always communicate with the user in Brazilian Portuguese.**

## What this is

Post-format automation for Linux/KDE (Arch/CachyOS/SteamOS, Debian/Ubuntu and Fedora/Bazzite): a modular Python CLI that replaces long shell scripts with numbered, auditable steps, each supporting `apply`, `dry-run`, `status`, and `undo`. All user-facing strings and log output are in Brazilian Portuguese (without accents) — keep new output consistent with that. Immutable systems (Bazzite/SteamOS) are detected via a `Distro.immutable` flag: native package installs are skipped (degraded) in favor of Flatpak, while `/etc` edits (fstab, sddm, udev) still apply.

## Commands

```fish
python -m pytest                       # run all tests
python -m pytest tests/test_steps.py  # run one test file
python -m pytest tests/test_steps.py -k name  # run one test
python -m py_compile 00-pos-formatacao-cachyos.py postformat/*.py  # syntax check

python 00-pos-formatacao-cachyos.py   # main interactive flow (bootstraps deps first)
python 00-pos-formatacao-cachyos.py --gui  # GUI (bootstraps PySide6 on demand)
python -m postformat.gui               # GUI directly (PySide6 already installed)
python -m postformat step 10 dry-run  # run a single step non-interactively
python -m postformat step 13 status
bash scripts/10-instalar-apps-jogos-comunicacao-dev.sh  # wrapper → opens that step's menu

QT_QPA_PLATFORM=offscreen python -m pytest tests/test_gui.py  # GUI tests headless
bash packaging/build-appimage.sh       # build the AppImage locally (needs .[gui] + pyinstaller)
```

There is no linter or formatter configured. Only runtime dependency is `InquirerPy` (TUI menus); `pytest` for tests. `postformat/bootstrap.py` auto-installs both at first run (system package → AUR → pip fallback).

## Architecture

Execution flows: entry point (`00-pos-formatacao-cachyos.py` or `python -m postformat`) → `bootstrap.ensure_bootstrap()` → `cli.main()` → step dispatch. The `scripts/*.sh` wrappers are 5-line shims that just call `python -m postformat step <ID> "${1:-menu}"`.

- **`postformat/steps.py`** (~1800 lines) — all 14 step classes (`00` ecosystem prep … `13` Sunshine), registered in the `ALL_STEPS` tuple at the bottom. Step IDs match the `scripts/NN-*.sh` wrapper names and the README table.
- **`postformat/steps_base.py`** — the `Step` base class and `StepContext`/`StepResult`. A step implements `apply()`, optionally `status()`/`undo()`; `dry_run()` is generic — it re-runs `apply()` with a dry-run `Runner`. Steps report outcomes via `mark_done/skipped/manual` (execution status) and `mark_applied/pending/attention` (compliance), plus `add_hint()`.
- **`postformat/core.py`** — `Runner` (the only sanctioned way to execute commands: handles dry-run echo, sudo, streaming output with spinner, NoNewPrivs detection, KeyboardInterrupt), `Logger` (console + `./LOGS/*.log`, ANSI stripped in files), ANSI `Color` palette, `write_text`/`write_text_sudo`/`backup_existing` helpers, `detect_user()` (resolves real user even under `SUDO_USER` — never hardcode home paths).
- **`postformat/platform.py`** — distro abstraction. `detect_distro()` maps `/etc/os-release` to family `arch`, `debian` or `fedora`, plus an orthogonal `immutable` flag (detected via `/run/ostree-booted` or `steamos-readonly`). Everything package-related (`install_system_package`, `install_system_or_aur`, `update_system`, `ensure_rpmfusion`, query commands) branches on that family. On immutable systems native installs are no-ops (warn + skip) so callers fall back to Flatpak. New package operations must support all three families or raise `UnsupportedDistroError`.
- **`postformat/cli.py`** — menus (full apply/dry-run/status, per-step actions), progress bars, summary rendering. `run_action()` is the single dispatch point for step actions.
- **`postformat/tui.py`** — InquirerPy menu wrapper with a numbered-input fallback; **`installers.py`** — higher-level install helpers (Flatpak, AppImage); **`desktop.py`** — `.desktop` entry rendering (note `StartupWMClass` matters for KDE Wayland window grouping).
- **`postformat/gui/`** — GUI frontend (PySide6/Qt6), a second frontend over the same engine; the CLI is untouched. It never reimplements step logic — it builds the same `StepContext` but swaps in a `GuiLogger` (emits Qt signals instead of printing) and configures the `Runner` with an askpass + an interactive-terminal executor. Key pieces: `main_window.py` (sidebar of `ALL_STEPS` + action panel + console/terminal), `step_runner.py` (`StepWorker` QThread running one action), `gui_logger.py`, `prompts.py` (`GuiInteraction` implementing `InteractionProvider` via dialogs), `askpass.py` (graphical `sudo -A`), `terminal.py` (pty-backed terminal + `TerminalExecutor` for `interactive_tty`), `updater.py` (GitHub Releases check), `theme.qss`. The I/O seam lives in `core.py`: `Logger._emit_console`, the optional `Logger.interaction` (`InteractionProvider`), and `Runner.askpass` / `Runner.interactive_executor` (`InteractiveExecutor`). The cross-thread pattern: worker emits a Qt signal and blocks on a `threading.Event` until the UI thread answers.

## GUI / packaging / release

- The GUI is an additive frontend: **never** move step logic into `postformat/gui/`. If a step needs new interaction, extend `InteractionProvider`/the I/O seam in `core.py`, not the GUI.
- PySide6 is an optional dependency (`.[gui]`), installed on demand by `bootstrap.ensure_gui_bootstrap()` only when `--gui` is used. GUI tests `pytest.importorskip("PySide6")` so the base CI gate stays green without it.
- Packaging lives in `packaging/`: PyInstaller (`sisteminha.spec`, `entry.py`) → AppDir (`AppRun`, `sisteminha.desktop`, icon from `make_icon.py` → `assets/sisteminha.png`) → `appimagetool` (`build-appimage.sh`). Version is injected into `postformat/gui/_version.py` at build time.
- `.github/workflows/ci.yml` has two jobs: `test` (gate: ruff + py_compile + pytest) and `release` (runs only on push to `main`, after `test`) which builds the AppImage and publishes a GitHub Release tagged `v1.0.<run_number>` with the `.AppImage`, `.zsync`, and `SHA256SUMS`. AppImage update info points back at `victorlcs87/scripts-linux` latest release for auto-update.

## Conventions

- Run everything as a normal user; `sudo` only goes through `Runner.run(..., sudo=True)`. Interactive commands (pacman/apt prompts) use `interactive_tty=True` with a `manual_message` so the user knows it's waiting, not hung.
- Every state change must be dry-run aware (the `Runner`/`write_text` helpers handle this — don't bypass them with raw `subprocess`), back up files before modifying (`backup_existing`), and require typed confirmation for dangerous operations like fstab edits (`confirm_phrase`).
- Tests stub `StepContext` with fake runners/loggers (see `tests/test_steps.py`) — steps never touch the real system in tests.
- `LOGS/`, `fstab-backups/`, and caches are local-only artifacts; never commit them.
- When adding a step: subclass `Step` in `steps.py`, append to `ALL_STEPS`, create the matching `scripts/NN-*.sh` wrapper, and update the README step table.
