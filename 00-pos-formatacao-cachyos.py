#!/usr/bin/env python3
import sys
from pathlib import Path

from postformat.bootstrap import BootstrapError, ensure_bootstrap, ensure_gui_bootstrap


def run() -> int:
    project_root = Path(__file__).resolve().parent
    use_gui = "--gui" in sys.argv[1:]
    try:
        ensure_bootstrap(project_root)
        if use_gui:
            ensure_gui_bootstrap(project_root)
    except BootstrapError as exc:
        print(f"[bootstrap] {exc}", file=sys.stderr)
        return 1
    if use_gui:
        from postformat.gui import main as gui_main

        return gui_main([sys.argv[0], *[arg for arg in sys.argv[1:] if arg != "--gui"]])
    from postformat.cli import main

    return main()


if __name__ == "__main__":
    raise SystemExit(run())
