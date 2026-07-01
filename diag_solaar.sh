#!/usr/bin/env bash
#
# diag_solaar.sh
# Script de DIAGNÓSTICO (somente coleta de informações) para investigar
# perda de configuração de DPI no Solaar / dispositivos Logitech.
#
# Não altera nada no sistema. Apenas coleta dados para análise.
#
# Regras seguidas: regras_criacao_scripts(1).md
#
set -euo pipefail

# ============================================================
# Configurações
# ============================================================
REAL_USER="${SUDO_USER:-$USER}"
USER_HOME="$(getent passwd "$REAL_USER" | cut -d: -f6)"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${SCRIPT_DIR}/LOGS"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="${LOG_DIR}/diag_solaar_${TIMESTAMP}.log"

SOLAAR_CONFIG="${USER_HOME}/.config/solaar/config.yaml"
SOLAAR_RULES="${USER_HOME}/.config/solaar/rules.yaml"

# ============================================================
# Cores
# ============================================================
VERDE='\033[0;32m'
AMARELO='\033[1;33m'
VERMELHO='\033[0;31m'
AZUL='\033[1;34m'
NC='\033[0m' # No Color

# ============================================================
# Funções
# ============================================================

titulo() {
    echo -e "\n${AZUL}== $1 ==${NC}"
}

sucesso() {
    echo -e "${VERDE}[OK]${NC} $1"
}

aviso() {
    echo -e "${AMARELO}[AVISO]${NC} $1"
}

erro() {
    echo -e "${VERMELHO}[ERRO]${NC} $1"
}

preparar_log_dir() {
    mkdir -p "$LOG_DIR"
}

coletar() {
    preparar_log_dir
    titulo "Coletando informações de diagnóstico do Solaar"

    {
        echo "### DIAGNOSTICO SOLAAR - $(date) ###"
        echo "Usuario: $REAL_USER"
        echo "Home: $USER_HOME"
        echo ""

        echo "--- Versao do Solaar ---"
        if command -v solaar &>/dev/null; then
            solaar --version 2>&1 || echo "Falha ao obter versao"
        else
            echo "Solaar nao encontrado no PATH"
        fi
        echo ""

        echo "--- Pacote instalado (pacman) ---"
        pacman -Qi solaar 2>&1 || echo "Pacote solaar nao encontrado via pacman"
        echo ""

        echo "--- solaar show (dispositivos e features) ---"
        if command -v solaar &>/dev/null; then
            solaar show 2>&1 || echo "Falha ao executar solaar show"
        fi
        echo ""

        echo "--- solaar config (valores atuais, se suportado) ---"
        if command -v solaar &>/dev/null; then
            solaar config 2>&1 || echo "Falha ao executar solaar config"
        fi
        echo ""

        echo "--- Conteudo de config.yaml ---"
        if [[ -f "$SOLAAR_CONFIG" ]]; then
            cat "$SOLAAR_CONFIG" 2>&1
        else
            echo "Arquivo nao encontrado: $SOLAAR_CONFIG"
        fi
        echo ""

        echo "--- Conteudo de rules.yaml ---"
        if [[ -f "$SOLAAR_RULES" ]]; then
            cat "$SOLAAR_RULES" 2>&1
        else
            echo "Arquivo nao encontrado: $SOLAAR_RULES"
        fi
        echo ""

        echo "--- Permissoes do diretorio ~/.config/solaar ---"
        ls -la "${USER_HOME}/.config/solaar" 2>&1 || echo "Diretorio nao encontrado"
        echo ""

        echo "--- Servico systemd do Solaar (user) ---"
        systemctl --user status solaar.service 2>&1 || echo "Servico solaar.service (user) nao encontrado/ativo"
        echo ""

        echo "--- Servico systemd do Solaar (system) ---"
        systemctl status solaar.service 2>&1 || echo "Servico solaar.service (system) nao encontrado/ativo"
        echo ""

        echo "--- Autostart do Solaar ---"
        find "${USER_HOME}/.config/autostart" -iname "*solaar*" 2>&1
        echo ""

        echo "--- Dispositivos USB Logitech (lsusb) ---"
        lsusb | grep -i logitech 2>&1 || echo "Nenhum dispositivo Logitech encontrado via lsusb"
        echo ""

        echo "--- Dispositivos HID relacionados (udevadm) ---"
        for dev in /dev/hidraw*; do
            [[ -e "$dev" ]] || continue
            echo "Dispositivo: $dev"
            udevadm info --query=all --name="$dev" 2>&1 | grep -i -E "logitech|product|vendor" || true
            echo "---"
        done
        echo ""

        echo "--- Regras udev relacionadas (Solaar/Logitech) ---"
        grep -ril -E "logitech|solaar" /usr/lib/udev/rules.d/ /etc/udev/rules.d/ 2>/dev/null | while read -r f; do
            echo "Arquivo: $f"
            cat "$f"
            echo "---"
        done
        echo ""

        echo "--- Logs do journalctl (Solaar, ultimas 200 linhas, sessao atual) ---"
        journalctl --user -u solaar.service -n 200 --no-pager 2>&1 || true
        journalctl -n 200 --no-pager --grep="solaar" 2>&1 || true
        echo ""

        echo "--- Logs do kernel (dmesg) relacionados a USB/HID Logitech ---"
        dmesg 2>&1 | grep -i -E "logitech|hid-logitech" || echo "Nenhuma entrada encontrada (pode exigir sudo)"
        echo ""

        echo "### FIM DA COLETA ###"

    } | tee "$LOG_FILE"

    echo ""
    sucesso "Log salvo em: $LOG_FILE"
}

status() {
    titulo "Status rápido"

    if command -v solaar &>/dev/null; then
        sucesso "Solaar instalado: $(solaar --version 2>&1 | head -n1)"
    else
        erro "Solaar não encontrado no PATH"
    fi

    if [[ -f "$SOLAAR_CONFIG" ]]; then
        sucesso "config.yaml encontrado em: $SOLAAR_CONFIG"
    else
        aviso "config.yaml NÃO encontrado em: $SOLAAR_CONFIG"
    fi

    if systemctl --user is-active --quiet solaar.service 2>/dev/null; then
        sucesso "Serviço solaar.service (user) está ativo"
    else
        aviso "Serviço solaar.service (user) não está ativo ou não existe"
    fi

    if [[ -d "$LOG_DIR" ]]; then
        sucesso "Pasta de logs existe: $LOG_DIR ($(find "$LOG_DIR" -type f | wc -l) arquivo(s))"
    else
        aviso "Pasta de logs ainda não foi criada"
    fi
}

# ============================================================
# Menu principal
# ============================================================
menu() {
    echo -e "${AZUL}=============================================="
    echo "  Diagnóstico Solaar - Perda de configuração DPI"
    echo -e "==============================================${NC}"
    echo "1) Coletar informações e gerar log"
    echo "2) Status rápido"
    echo "3) Sair"
    echo -n "Escolha uma opção: "
    read -r opcao

    case "$opcao" in
        1) coletar ;;
        2) status ;;
        3) echo "Saindo..."; exit 0 ;;
        *) erro "Opção inválida"; menu ;;
    esac
}

menu
