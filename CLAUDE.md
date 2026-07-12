# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Always communicate with the user in Brazilian Portuguese.**

## What this is

Post-format automation for Linux/KDE (Arch/CachyOS/SteamOS, Debian/Ubuntu and Fedora/Bazzite): a modular Python CLI that replaces long shell scripts with auditable steps, each supporting the user-facing actions `apply`, `status`, and `undo`. (There is still an internal `dry-run` Runner mode used by the test suite and safe previews, but it is **not** offered as a user action in the menus.) All user-facing strings and log output are in Brazilian Portuguese (without accents) — keep new output consistent with that. Immutable systems (Bazzite/SteamOS) are detected via a `Distro.immutable` flag: native package installs are skipped (degraded) in favor of Flatpak, while `/etc` edits (fstab, sddm, udev) still apply.

## Commands

```fish
python -m pytest                       # run all tests
python -m pytest tests/test_steps.py  # run one test file
python -m pytest tests/test_steps.py -k name  # run one test
python -m ruff check . && python -m ruff format --check .  # lint + format (mesmo gate do CI)
python -m py_compile 00-pos-formatacao-cachyos.py reforja/*.py reforja/steps/*.py reforja/gui/*.py  # syntax check

python 00-pos-formatacao-cachyos.py   # main interactive flow (bootstraps deps first)
python 00-pos-formatacao-cachyos.py --gui  # GUI (bootstraps PySide6 on demand)
python -m reforja.gui               # GUI directly (PySide6 already installed)
python -m reforja step 10 status   # run a single step non-interactively (id kept for scripting)
python -m reforja step 13 status
bash scripts/10-instalar-apps-jogos-comunicacao-dev.sh  # wrapper → opens that step's menu

QT_QPA_PLATFORM=offscreen python -m pytest tests/test_gui.py  # GUI tests headless
bash packaging/build-appimage.sh       # build the AppImage locally (needs .[gui] + pyinstaller)
```

Lint/format: `ruff check` + `ruff format --check` (both gate the CI — run them before calling work done). Only runtime dependency is `InquirerPy` (TUI menus); dev deps (`pytest`, `ruff`) come from `pip install -e .[dev]`. `reforja/bootstrap.py` auto-installs only the runtime dep at first run (system package → AUR → pip fallback).

## Architecture

Execution flows: entry point (`00-pos-formatacao-cachyos.py` or `python -m reforja`) → `bootstrap.ensure_bootstrap()` → `cli.main()` → step dispatch. The `scripts/*.sh` wrappers are 5-line shims that just call `python -m reforja step <ID> "${1:-menu}"`.

- **`reforja/steps/`** (package) — step classes by domain: `system.py` (00), `browser.py` (03 Navegador+WebApps), `gaming.py` (05 GPU, 10 Apps, 13 Sunshine), `dev.py` (06 Git/GitHub, 12 Antigravity), `storage.py` (07 rclone, 08 fstab), `kde.py` (09 Ajustes KDE: gestos+NumLock), `inventory.py` (14), `appimage.py` (15). Registered in `ALL_STEPS`/`ALL_GROUPS` (`steps/__init__.py`); shared helpers in `steps/_common.py` (`header`, `InputGroupMixin`). Step IDs match the `scripts/NN-*.sh` wrapper names and the README table; ID gaps (01, 02, 04, 11) come from merges/removals.
- **`reforja/steps_base.py`** — the `Step` base class, `StepTask`, and `StepContext`/`StepResult`. **A step declares `tasks()`, it does not hand-roll `apply()`.** Each `StepTask` carries a `key`, a `label`, a `description` (the full user-facing explanation of that one item), a `run` callable, and a `detect` callable answering "is this already done?" (returns `bool`, or a string used as the detail shown next to the label). Flags: `stateless` (an action with no state, e.g. "update the system"), `available`/`unavailable_reason` (doesn't apply to this machine, e.g. touchpad gestures on a desktop), `destructive` (removes things — **never** pre-checked, not even in "Aplicar tudo"). The base `apply()` probes via `plan()`, shows a checkbox **pre-checked with what is already applied** (checked = run/re-apply; unchecked = leave alone — removal only via Undo), runs each chosen task isolated in try/except, then re-probes to set compliance. The base `status()` reports the same plan. A single-task step skips the checkbox (choosing the step is choosing the item). Set `compliance_from_plan = False` when the verdict comes from the step's own diagnostics (GPU, fstab, Git, hardware) and call `mark_*` yourself. Steps still report via `mark_done/skipped/manual` (execution) and `mark_applied/pending/attention` (compliance), plus `add_hint()`.
- **`reforja/planning.py`** — probing + selection *before* execution: `collect_plans()` (probe N steps), `prompt_global_selection()` (the single consolidated screen used by `Aplicar tudo`, everything pre-checked; and by a multi-step Apply, pre-checked with what's applied) returning `{step_id: (task_keys,)}` which the frontends inject into `Step.selection` to skip the step's own checkbox. `render_step_explanation()` (ANSI, for the CLI) / `describe_step_plain()` (plain text, for the GUI console) render the full per-item explanation. The GUI passes **unprobed** tasks to it on purpose — probing can need sudo, and merely clicking a step must not prompt for a password.
- **`reforja/core.py`** — `Runner` (the only sanctioned way to execute commands: handles dry-run echo, sudo, streaming output with spinner, NoNewPrivs detection, KeyboardInterrupt), `Logger` (console + `./LOGS/*.log`, ANSI stripped in files), ANSI `Color` palette, `write_text`/`write_text_sudo`/`backup_existing` helpers, `detect_user()` (resolves real user even under `SUDO_USER` — never hardcode home paths).
- **`reforja/platform.py`** — distro abstraction. `detect_distro()` maps `/etc/os-release` to family `arch`, `debian` or `fedora`, plus an orthogonal `immutable` flag (detected via `/run/ostree-booted` or `steamos-readonly`). Everything package-related (`install_system_package`, `install_system_or_aur`, `update_system`, `ensure_rpmfusion`, query commands) branches on that family. On immutable systems native installs are no-ops (warn + skip) so callers fall back to Flatpak. New package operations must support all three families or raise `UnsupportedDistroError`.
- **`reforja/cli.py`** — flat menu: `Aplicar tudo` / `Status geral` / `Executar etapas...` (multi-select via `choose_multiple`, then a single action `Aplicar`/`Status`/`Undo`) / `Instalar GUI do Reforja no sistema` (shortcut: runs step 15 with `preselect_names=("Reforja",)`) / `Sair`. Steps are shown by title only (no numbers). Progress bars + summary rendering. `run_action()` builds the step and delegates to **`reforja/dispatch.py`** (`dispatch_action` + `finalize_result`), shared with the GUI's `StepWorker` — change dispatch/summary behavior there, not in the frontends.
- **`reforja/tui.py`** — InquirerPy menu wrapper with a numbered-input fallback; **`installers.py`** — mechanism-level helpers only (Flatpak, npm, `fetch_json`, `install_system_or_flatpak`); package-manager installs live in `platform.py` — import from there, `installers` does not re-export it; **`desktop.py`** — `.desktop` entry rendering (note `StartupWMClass` matters for KDE Wayland window grouping).
- **`reforja/gui/`** — GUI frontend (PySide6/Qt6), a second frontend over the same engine; the CLI is untouched. It never reimplements step logic — it builds the same `StepContext` but swaps in a `GuiLogger` (emits Qt signals instead of printing) and configures the `Runner` with an askpass + an interactive-terminal executor. Key pieces: `main_window.py` (sidebar of `ALL_STEPS` + action panel + console/terminal), `step_runner.py` (`StepWorker` QThread running one action), `gui_logger.py`, `prompts.py` (`GuiInteraction` implementing `InteractionProvider` via dialogs), `askpass.py` (graphical `sudo -A`), `terminal.py` (pty-backed terminal + `TerminalExecutor` for `interactive_tty`), `updater.py` (GitHub Releases check), `theme.qss`. The I/O seam lives in `core.py`: `Logger._emit_console`, the optional `Logger.interaction` (`InteractionProvider`), and `Runner.askpass` / `Runner.interactive_executor` (`InteractiveExecutor`). The cross-thread pattern: worker emits a Qt signal and blocks on a `threading.Event` until the UI thread answers.

## GUI / packaging / release

- The GUI is an additive frontend: **never** move step logic into `reforja/gui/`. If a step needs new interaction, extend `InteractionProvider`/the I/O seam in `core.py`, not the GUI.
- PySide6 is an optional dependency (`.[gui]`), installed on demand by `bootstrap.ensure_gui_bootstrap()` only when `--gui` is used. GUI tests `pytest.importorskip("PySide6")` so the base CI gate stays green without it.
- Packaging lives in `packaging/`: PyInstaller (`reforja.spec`, `entry.py`) → AppDir (`AppRun`, `reforja.desktop`, icon from `make_icon.py` → `assets/reforja.png`) → `appimagetool` (`build-appimage.sh`). Version is injected into `reforja/gui/_version.py` at build time.
- `.github/workflows/ci.yml` has two jobs: `test` (gate: ruff + py_compile + pytest) and `release` (runs only on push to `main`, after `test`) which builds the AppImage and publishes a GitHub Release tagged `v1.0.<run_number>` with the `.AppImage`, `.zsync`, and `SHA256SUMS`. AppImage update info points back at `victorlcs87/scripts-linux` latest release for auto-update.

## Conventions

- Run everything as a normal user; `sudo` only goes through `Runner.run(..., sudo=True)`. Interactive commands (pacman/apt prompts) use `interactive_tty=True` with a `manual_message` so the user knows it's waiting, not hung.
- Every state change must be dry-run aware (the `Runner`/`write_text` helpers handle this — don't bypass them with raw `subprocess`), back up files before modifying (`backup_existing`), and require typed confirmation for dangerous operations like fstab edits (`confirm_phrase`).
- Tests stub `StepContext` with fake runners/loggers (see `tests/test_steps.py`) — steps never touch the real system in tests.
- `LOGS/`, `fstab-backups/`, and caches are local-only artifacts; never commit them.
- When adding a step: subclass `Step` in the right `steps/*.py` module, implement `tasks()` (do **not** write a bespoke `apply()` unless the step needs a preamble — then call `super().apply()`), append to `ALL_STEPS` and a group in `ALL_GROUPS`, create the matching `scripts/NN-*.sh` wrapper, and update the README step table.
- Every `StepTask` needs a `description` that fully explains what that item does, and a `detect` that is honest about the machine's current state — the pre-checked boxes users see are exactly `detect() == True`. Anything that removes packages/files must be `destructive=True`.
