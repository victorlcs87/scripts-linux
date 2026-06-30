#!/usr/bin/env python3
"""Ponto de entrada congelado pelo PyInstaller para o AppImage."""

import sys

from postformat.gui import main

if __name__ == "__main__":
    sys.exit(main())
