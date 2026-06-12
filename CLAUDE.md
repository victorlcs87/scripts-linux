# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Always communicate with the user in Brazilian Portuguese.**

## What this is

Post-format automation for Linux/KDE (Arch/CachyOS and Debian/Ubuntu): a modular Python CLI that replaces long shell scripts with numbered, auditable steps, each supporting `apply`, `dry-run`, `status`, and `undo`. All user-facing strings and log output are in Brazilian Portuguese (without accents) — keep new output consistent with that.

## Commands

```fish
python -m pytest                       # run all tests
python -m pytest tests/test_steps.py  # run one test file
python -m pytest tests/test_steps.py -k name  # run one test
python -m py_compile 00-pos-formatacao-cachyos.py postformat/*.py  # syntax check

python 00-pos-formatacao-cachyos.py   # main interactive flow (bootstraps deps first)
python -m postformat step 10 dry-run  # run a single step non-interactively
python -m postformat step 13 status
bash scripts/10-instalar-apps-jogos-comunicacao-dev.sh  # wrapper → opens that step's menu
```

There is no linter or formatter configured. Only runtime dependency is `InquirerPy` (TUI menus); `pytest` for tests. `postformat/bootstrap.py` auto-installs both at first run (system package → AUR → pip fallback).

## Architecture

Execution flows: entry point (`00-pos-formatacao-cachyos.py` or `python -m postformat`) → `bootstrap.ensure_bootstrap()` → `cli.main()` → step dispatch. The `scripts/*.sh` wrappers are 5-line shims that just call `python -m postformat step <ID> "${1:-menu}"`.

- **`postformat/steps.py`** (~1800 lines) — all 14 step classes (`00` ecosystem prep … `13` Sunshine), registered in the `ALL_STEPS` tuple at the bottom. Step IDs match the `scripts/NN-*.sh` wrapper names and the README table.
- **`postformat/steps_base.py`** — the `Step` base class and `StepContext`/`StepResult`. A step implements `apply()`, optionally `status()`/`undo()`; `dry_run()` is generic — it re-runs `apply()` with a dry-run `Runner`. Steps report outcomes via `mark_done/skipped/manual` (execution status) and `mark_applied/pending/attention` (compliance), plus `add_hint()`.
- **`postformat/core.py`** — `Runner` (the only sanctioned way to execute commands: handles dry-run echo, sudo, streaming output with spinner, NoNewPrivs detection, KeyboardInterrupt), `Logger` (console + `./LOGS/*.log`, ANSI stripped in files), ANSI `Color` palette, `write_text`/`write_text_sudo`/`backup_existing` helpers, `detect_user()` (resolves real user even under `SUDO_USER` — never hardcode home paths).
- **`postformat/platform.py`** — distro abstraction. `detect_distro()` maps `/etc/os-release` to family `arch` or `debian`; everything package-related (`install_system_package`, `install_system_or_aur`, `update_system`, query commands) branches on that family. New package operations must support both families or raise `UnsupportedDistroError`.
- **`postformat/cli.py`** — menus (full apply/dry-run/status, per-step actions), progress bars, summary rendering. `run_action()` is the single dispatch point for step actions.
- **`postformat/tui.py`** — InquirerPy menu wrapper with a numbered-input fallback; **`installers.py`** — higher-level install helpers (Flatpak, AppImage); **`desktop.py`** — `.desktop` entry rendering (note `StartupWMClass` matters for KDE Wayland window grouping).

## Conventions

- Run everything as a normal user; `sudo` only goes through `Runner.run(..., sudo=True)`. Interactive commands (pacman/apt prompts) use `interactive_tty=True` with a `manual_message` so the user knows it's waiting, not hung.
- Every state change must be dry-run aware (the `Runner`/`write_text` helpers handle this — don't bypass them with raw `subprocess`), back up files before modifying (`backup_existing`), and require typed confirmation for dangerous operations like fstab edits (`confirm_phrase`).
- Tests stub `StepContext` with fake runners/loggers (see `tests/test_steps.py`) — steps never touch the real system in tests.
- `LOGS/`, `fstab-backups/`, and caches are local-only artifacts; never commit them.
- When adding a step: subclass `Step` in `steps.py`, append to `ALL_STEPS`, create the matching `scripts/NN-*.sh` wrapper, and update the README step table.
