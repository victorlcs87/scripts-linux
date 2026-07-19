#!/usr/bin/env bash
# Bateria completa dentro do container: gate + testes de GUI + driver funcional
# da GUI ponta a ponta contra o sistema real do container.
set -euo pipefail
cd /work
# O driver e um script fora da raiz; garante que 'import reforja' resolva /work.
export PYTHONPATH=/work

echo "==================== ruff ===================="
python -m ruff check .
python -m ruff format --check .

echo "==================== py_compile ===================="
python -m py_compile 00-pos-formatacao-cachyos.py reforja/*.py reforja/steps/*.py reforja/gui/*.py

echo "==================== pytest (motor, compartilhado) ===================="
python -m pytest -q

echo "==================== pytest (GUI, offscreen) ===================="
python -m pytest tests/test_gui.py -q

echo "==================== driver funcional da GUI (sistema real) ===================="
python packaging/test-container/gui_drive.py

echo "==================== TUDO VERDE ===================="
