#!/usr/bin/env bash
# Constroi e roda a bateria completa do Reforja nas tres familias suportadas:
# Arch, Debian e Fedora. Cada uma num container limpo, exercitando toda a GUI
# ponta a ponta contra o sistema real daquela distro.
#
#   bash packaging/test-container/test-all-distros.sh          # todas
#   bash packaging/test-container/test-all-distros.sh debian   # so uma (ou mais)
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"

distros=("$@")
if [ ${#distros[@]} -eq 0 ]; then
    distros=(arch debian fedora)
fi

fail=0
for distro in "${distros[@]}"; do
    echo ""
    echo "######################## $distro ########################"
    if ! docker build -t "reforja-test-$distro" -f "$here/Dockerfile.$distro" "$here"; then
        echo ">>> build FALHOU: $distro"
        fail=1
        continue
    fi
    if ! docker run --rm -v "$repo":/work "reforja-test-$distro" bash packaging/test-container/run-all.sh; then
        echo ">>> testes FALHARAM: $distro"
        fail=1
    fi
done

echo ""
if [ "$fail" -ne 0 ]; then
    echo "======== ALGUMA DISTRO FALHOU ========"
    exit 1
fi
echo "======== TODAS AS DISTROS VERDES ========"
