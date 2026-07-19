#!/usr/bin/env bash
# Teste de instalacao REAL (opt-in): instala/remove um pacote leve de verdade no
# container, exercitando o caminho real do motor (sudo + gerenciador de pacotes).
# NAO faz parte do run-all.sh nem do CI: precisa de rede e e mais lento.
#
#   docker run --rm -v "$PWD":/work reforja-test-arch bash packaging/test-container/run-real-install.sh
set -euo pipefail
cd /work
export PYTHONPATH=/work

echo "==================== atualizando indices de pacote ===================="
if command -v pacman >/dev/null; then sudo pacman -Sy --noconfirm >/dev/null; fi
if command -v apt-get >/dev/null; then sudo apt-get update -qq; fi
# dnf atualiza o indice sozinho na instalacao.

echo "==================== instalacao real (tree) ===================="
# O pacman (Arch) confirma a instalacao de forma interativa (o produto usa
# interactive_tty de proposito); num container headless nao ha quem responda,
# entao alimentamos 'y' via `yes`. apt/dnf ja usam -y e ignoram a entrada.
yes | python packaging/test-container/real_install.py

echo "==================== OK ===================="
