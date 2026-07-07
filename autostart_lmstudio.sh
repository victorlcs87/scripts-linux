#!/usr/bin/env bash
###############################################################################
# autostart_lmstudio.sh
#
# Configura o LM Studio para subir automaticamente (servidor + modelo já
# carregado com o contexto desejado) via systemd --user no login.
#
# Reversível: cria backup do que existir antes de alterar, e tem opção Undo
# para remover tudo e restaurar o estado anterior.
#
# Uso:
#   bash autostart_lmstudio.sh
###############################################################################

set -euo pipefail

###############################
# Configurações (edite aqui antes de usar)
###############################
MODEL_KEY="qwen3.5-9b-instruct"     # Rode 'lms ls' para ver os model_key disponíveis
CONTEXT_LENGTH="65536"              # >= 64000 é o mínimo exigido pelo Hermes Agent
GPU_MODE="max"                      # max | off | 0.0-1.0
IDENTIFIER="hermes-default"         # Nome que o Hermes vai usar pra referenciar o modelo
SERVER_STARTUP_WAIT="6"             # Segundos de espera após subir o servidor

SERVICE_NAME="lmstudio-hermes.service"
WRAPPER_NAME="start-lmstudio-hermes.sh"

###############################
# Variáveis
###############################
REAL_USER="${SUDO_USER:-$USER}"
USER_HOME="$(getent passwd "$REAL_USER" | cut -d: -f6)"

SYSTEMD_USER_DIR="${USER_HOME}/.config/systemd/user"
WRAPPER_DIR="${USER_HOME}/.local/bin"
SERVICE_PATH="${SYSTEMD_USER_DIR}/${SERVICE_NAME}"
WRAPPER_PATH="${WRAPPER_DIR}/${WRAPPER_NAME}"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LOG_DIR="./LOGS/autostart_lmstudio_${TIMESTAMP}"
BACKUP_DIR="${LOG_DIR}/backup"

###############################
# Cores
###############################
COR_RESET="\033[0m"
COR_VERDE="\033[0;32m"
COR_AMARELO="\033[0;33m"
COR_VERMELHO="\033[0;31m"
COR_TITULO="\033[1;36m"

###############################
# Funções auxiliares
###############################

log_msg() { echo -e "$1"; }
titulo() { log_msg "${COR_TITULO}\n==== $1 ====${COR_RESET}"; }
ok() { log_msg "${COR_VERDE}[OK]${COR_RESET} $1"; }
aviso() { log_msg "${COR_AMARELO}[AVISO]${COR_RESET} $1"; }
erro() { log_msg "${COR_VERMELHO}[ERRO]${COR_RESET} $1"; }
comando_existe() { command -v "$1" >/dev/null 2>&1; }

localizar_lms() {
    if comando_existe lms; then
        command -v lms
        return 0
    fi
    if [ -x "${USER_HOME}/.lmstudio/bin/lms" ]; then
        echo "${USER_HOME}/.lmstudio/bin/lms"
        return 0
    fi
    return 1
}

###############################
# Funções principais
###############################

status() {
    titulo "Status atual"

    local lms_path
    if lms_path="$(localizar_lms)"; then
        ok "lms encontrado em: ${lms_path}"
    else
        erro "lms não encontrado no PATH nem em ~/.lmstudio/bin/. Abra o LM Studio pelo menos uma vez para inicializar o CLI."
    fi

    if [ -f "$SERVICE_PATH" ]; then
        ok "Unit systemd já existe: ${SERVICE_PATH}"
        echo "--- Conteúdo atual ---"
        cat "$SERVICE_PATH"
    else
        aviso "Unit systemd ainda não configurada (${SERVICE_PATH} não existe)"
    fi

    if [ -f "$WRAPPER_PATH" ]; then
        ok "Wrapper script existe: ${WRAPPER_PATH}"
    else
        aviso "Wrapper script ainda não existe"
    fi

    echo ""
    if systemctl --user is-enabled "$SERVICE_NAME" >/dev/null 2>&1; then
        ok "Serviço está HABILITADO para iniciar no login"
    else
        aviso "Serviço não está habilitado"
    fi

    if systemctl --user is-active "$SERVICE_NAME" >/dev/null 2>&1; then
        ok "Serviço está ATIVO agora"
    else
        aviso "Serviço não está rodando agora"
    fi
}

dry_run() {
    titulo "Dry-run (nada será alterado)"

    echo "Seria criado o wrapper em: ${WRAPPER_PATH}"
    echo "--------------------------------------------------"
    echo "#!/usr/bin/env bash"
    echo "set -euo pipefail"
    echo "lms server start"
    echo "sleep ${SERVER_STARTUP_WAIT}"
    echo "lms load \"${MODEL_KEY}\" --context-length ${CONTEXT_LENGTH} --gpu ${GPU_MODE} --identifier \"${IDENTIFIER}\""
    echo "--------------------------------------------------"
    echo ""
    echo "Seria criada a unit systemd em: ${SERVICE_PATH}"
    echo "--------------------------------------------------"
    echo "[Unit]"
    echo "Description=LM Studio server + modelo pré-carregado para Hermes Agent"
    echo "After=graphical-session.target"
    echo ""
    echo "[Service]"
    echo "Type=simple"
    echo "ExecStart=${WRAPPER_PATH}"
    echo "Restart=on-failure"
    echo "RestartSec=5"
    echo ""
    echo "[Install]"
    echo "WantedBy=default.target"
    echo "--------------------------------------------------"
    aviso "Nenhuma alteração foi feita (modo dry-run)."
}

fazer_backup() {
    mkdir -p "$BACKUP_DIR"
    if [ -f "$SERVICE_PATH" ]; then
        cp "$SERVICE_PATH" "${BACKUP_DIR}/$(basename "$SERVICE_PATH").bak"
        ok "Backup da unit systemd salvo em: ${BACKUP_DIR}"
    fi
    if [ -f "$WRAPPER_PATH" ]; then
        cp "$WRAPPER_PATH" "${BACKUP_DIR}/$(basename "$WRAPPER_PATH").bak"
        ok "Backup do wrapper salvo em: ${BACKUP_DIR}"
    fi
}

apply() {
    titulo "Aplicando configuração de autostart"

    if ! localizar_lms >/dev/null; then
        erro "lms não encontrado. Abra o LM Studio pelo menos uma vez antes de continuar."
        return 1
    fi

    mkdir -p "$LOG_DIR"
    fazer_backup

    mkdir -p "$WRAPPER_DIR" "$SYSTEMD_USER_DIR"

    cat > "$WRAPPER_PATH" << EOF
#!/usr/bin/env bash
set -euo pipefail
lms server start
sleep ${SERVER_STARTUP_WAIT}
lms load "${MODEL_KEY}" --context-length ${CONTEXT_LENGTH} --gpu ${GPU_MODE} --identifier "${IDENTIFIER}"
EOF
    chmod +x "$WRAPPER_PATH"
    ok "Wrapper criado: ${WRAPPER_PATH}"

    cat > "$SERVICE_PATH" << EOF
[Unit]
Description=LM Studio server + modelo pré-carregado para Hermes Agent
After=graphical-session.target

[Service]
Type=simple
ExecStart=${WRAPPER_PATH}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
    ok "Unit systemd criada: ${SERVICE_PATH}"

    systemctl --user daemon-reload
    systemctl --user enable --now "$SERVICE_NAME"
    ok "Serviço habilitado e iniciado."

    echo ""
    aviso "Confirme com a opção 'Status' em alguns segundos, ou rode:"
    echo "  curl http://localhost:1234/v1/models"
}

undo() {
    titulo "Desfazendo autostart"

    if systemctl --user is-active "$SERVICE_NAME" >/dev/null 2>&1; then
        systemctl --user stop "$SERVICE_NAME"
        ok "Serviço parado"
    fi

    if systemctl --user is-enabled "$SERVICE_NAME" >/dev/null 2>&1; then
        systemctl --user disable "$SERVICE_NAME"
        ok "Serviço desabilitado"
    fi

    if [ -f "$SERVICE_PATH" ]; then
        rm -f "$SERVICE_PATH"
        ok "Unit systemd removida"
    fi

    if [ -f "$WRAPPER_PATH" ]; then
        rm -f "$WRAPPER_PATH"
        ok "Wrapper removido"
    fi

    systemctl --user daemon-reload
    ok "Undo concluído. Backups (se existiam) continuam em ./LOGS/*/backup"
}

###############################
# Menu principal
###############################

menu() {
    echo -e "${COR_TITULO}"
    echo "==================================================="
    echo "  Autostart LM Studio + Modelo (Hermes Agent)"
    echo "==================================================="
    echo -e "${COR_RESET}"
    echo "Modelo configurado: ${MODEL_KEY} | Contexto: ${CONTEXT_LENGTH} | GPU: ${GPU_MODE}"
    echo ""
    echo "1) Status"
    echo "2) Dry-run (mostrar o que seria feito, sem alterar nada)"
    echo "3) Apply (criar e habilitar o autostart)"
    echo "4) Undo (remover autostart e restaurar estado anterior)"
    echo "5) Sair"
    echo ""
    read -rp "Escolha uma opção [1-5]: " opcao

    case "$opcao" in
        1) status ;;
        2) dry_run ;;
        3) apply ;;
        4) undo ;;
        5) echo "Saindo."; exit 0 ;;
        *) erro "Opção inválida" ;;
    esac
}

menu
