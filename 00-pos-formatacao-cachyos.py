#!/usr/bin/env python3
import sys
from pathlib import Path

from postformat.bootstrap import BootstrapError, ensure_bootstrap


def run() -> int:
    try:
        ensure_bootstrap(Path(__file__).resolve().parent)
    except BootstrapError as exc:
        print(f"[bootstrap] {exc}", file=sys.stderr)
        return 1
    from postformat.cli import main

    return main()


if __name__ == "__main__":
    raise SystemExit(run())
