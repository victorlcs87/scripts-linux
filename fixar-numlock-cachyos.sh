#!/usr/bin/env bash
set -Eeuo pipefail

LOG_FILE="$PWD/fixar-numlock-cachyos_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1

SDDM_CONF="/etc/sddm.conf.d/20-numlock-fixo.conf"
KDE_CONF="$HOME/.config/kcminputrc"

ACTION="${1:-apply}"

echo "Log: $LOG_FILE"
echo "Ação: $ACTION"
echo

apply_numlock() {
    echo "==> Ativando Num Lock no KDE/Plasma..."

    if command -v kwriteconfig6 >/dev/null 2>&1; then
        kwriteconfig6 --file kcminputrc --group Keyboard --key NumLock 0
    elif command -v kwriteconfig5 >/dev/null 2>&1; then
        kwriteconfig5 --file kcminputrc --group Keyboard --key NumLock 0
    else
        mkdir -p "$(dirname "$KDE_CONF")"
        if grep -q '^\[Keyboard\]' "$KDE_CONF" 2>/dev/null; then
            sed -i '/^\[Keyboard\]/,/^\[/ s/^NumLock=.*/NumLock=0/' "$KDE_CONF"
            grep -q '^NumLock=0' "$KDE_CONF" || sed -i '/^\[Keyboard\]/a NumLock=0' "$KDE_CONF"
        else
            {
                echo
                echo "[Keyboard]"
                echo "NumLock=0"
            } >> "$KDE_CONF"
        fi
    fi

    echo "==> Ativando Num Lock no SDDM, tela de login..."
    sudo mkdir -p /etc/sddm.conf.d

    sudo tee "$SDDM_CONF" >/dev/null <<'EOF'
[General]
Numlock=on
EOF

    echo
    echo "Concluído."
    echo "Reinicie ou faça logout/login para testar."
}

revert_numlock() {
    echo "==> Revertendo configuração criada para o SDDM..."

    if [[ -f "$SDDM_CONF" ]]; then
        sudo rm -f "$SDDM_CONF"
        echo "Removido: $SDDM_CONF"
    else
        echo "Arquivo do SDDM não existia: $SDDM_CONF"
    fi

    echo "==> Deixando KDE/Plasma sem forçar Num Lock..."

    if command -v kwriteconfig6 >/dev/null 2>&1; then
        kwriteconfig6 --file kcminputrc --group Keyboard --key NumLock 2
    elif command -v kwriteconfig5 >/dev/null 2>&1; then
        kwriteconfig5 --file kcminputrc --group Keyboard --key NumLock 2
    else
        echo "kwriteconfig não encontrado. Ajuste manualmente em Configurações do Sistema > Teclado."
    fi

    echo
    echo "Reversão concluída."
}

status_numlock() {
    echo "==> Status KDE:"
    grep -n "NumLock" "$KDE_CONF" 2>/dev/null || echo "Nenhuma configuração NumLock encontrada em $KDE_CONF"

    echo
    echo "==> Status SDDM:"
    if [[ -f "$SDDM_CONF" ]]; then
        cat "$SDDM_CONF"
    else
        echo "Arquivo não existe: $SDDM_CONF"
    fi
}

case "$ACTION" in
    apply)
        apply_numlock
        ;;
    revert)
        revert_numlock
        ;;
    status)
        status_numlock
        ;;
    *)
        echo "Uso:"
        echo "  ./fixar-numlock-cachyos.sh apply"
        echo "  ./fixar-numlock-cachyos.sh revert"
        echo "  ./fixar-numlock-cachyos.sh status"
        exit 1
        ;;
esac
