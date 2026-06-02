#!/usr/bin/env bash

set -u

SCRIPT_NAME="$(basename "$0")"
BASE_DIR="$(pwd)"
LOG_DIR="$BASE_DIR/LOGS"
TS="$(date +%Y%m%d-%H%M%S)"
RUN_DIR="$LOG_DIR/correcao-apps-lentos-kde-$TS"
LOG_FILE="$RUN_DIR/correcao.log"
BACKUP_DIR="$RUN_DIR/backup"

RED="\033[31m"
GREEN="\033[32m"
YELLOW="\033[33m"
BLUE="\033[34m"
MAGENTA="\033[35m"
CYAN="\033[36m"
BOLD="\033[1m"
RESET="\033[0m"

mkdir -p "$RUN_DIR" "$BACKUP_DIR"

log() {
  echo -e "$1" | tee -a "$LOG_FILE"
}

title() {
  echo | tee -a "$LOG_FILE"
  log "${BOLD}${CYAN}============================================================${RESET}"
  log "${BOLD}${CYAN}$1${RESET}"
  log "${BOLD}${CYAN}============================================================${RESET}"
}

run_cmd() {
  local desc="$1"
  shift

  title "$desc"
  {
    echo "\$ $*"
    "$@"
  } >> "$LOG_FILE" 2>&1
}

run_shell() {
  local desc="$1"
  local cmd="$2"

  title "$desc"
  {
    echo "\$ $cmd"
    bash -lc "$cmd"
  } >> "$LOG_FILE" 2>&1
}

pause() {
  echo
  read -rp "Pressione ENTER para continuar..."
}

header() {
  clear
  echo -e "${BOLD}${MAGENTA}Correção leve de lentidão ao abrir apps - CachyOS KDE${RESET}"
  echo
  echo -e "${CYAN}Log:${RESET} $LOG_FILE"
  echo
}

status() {
  header

  title "Status rápido"

  echo -e "${BOLD}Sessão:${RESET}"
  echo "XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-indefinido}"
  echo "XDG_CURRENT_DESKTOP=${XDG_CURRENT_DESKTOP:-indefinido}"
  echo "KDE_SESSION_VERSION=${KDE_SESSION_VERSION:-indefinido}"
  echo

  echo -e "${BOLD}Serviços com falha:${RESET}"
  systemctl --failed --no-pager || true
  echo
  systemctl --user --failed --no-pager || true
  echo

  echo -e "${BOLD}Portal:${RESET}"
  systemctl --user status xdg-desktop-portal --no-pager 2>/dev/null | sed -n '1,18p' || true
  echo

  echo -e "${BOLD}Pacotes de portal instalados:${RESET}"
  pacman -Q xdg-desktop-portal xdg-desktop-portal-kde xdg-desktop-portal-gtk 2>/dev/null || true
  echo

  echo -e "${BOLD}Baloo:${RESET}"
  if command -v balooctl6 >/dev/null 2>&1; then
    balooctl6 status || true
  elif command -v balooctl >/dev/null 2>&1; then
    balooctl status || true
  else
    echo "balooctl não encontrado."
  fi

  pause
}

dry_run() {
  header

  echo -e "${YELLOW}Nada será alterado.${RESET}"
  echo
  echo "O Apply fará:"
  echo
  echo "1. Criar backup dos caches KDE/Plasma do usuário."
  echo "2. Reinstalar/revalidar pacotes principais:"
  echo "   - xdg-desktop-portal"
  echo "   - xdg-desktop-portal-kde"
  echo "   - xdg-desktop-portal-gtk"
  echo "   - plasma-workspace"
  echo "   - plasma-desktop"
  echo "   - kdeplasma-addons"
  echo "3. Recriar cache de menus/serviços com kbuildsycoca6."
  echo "4. Reiniciar serviços de portal do usuário."
  echo "5. Mover caches KDE/Plasma problemáticos para backup."
  echo
  echo "Não altera fstab, NVIDIA, bootloader, firewall nem arquivos de configuração principais do Plasma."
  echo
  echo "Backup será salvo em:"
  echo "$BACKUP_DIR"

  pause
}

backup_and_move_cache() {
  title "Movendo caches KDE/Plasma para backup"

  local items=(
    "$HOME/.cache/ksycoca6_"*
    "$HOME/.cache/plasmashell"*
    "$HOME/.cache/plasma_theme_"*
    "$HOME/.cache/kio_http"*
    "$HOME/.cache/icon-cache.kcache"
  )

  for item in "${items[@]}"; do
    if [[ -e "$item" ]]; then
      log "${YELLOW}Movendo:${RESET} $item"
      mv "$item" "$BACKUP_DIR/" >> "$LOG_FILE" 2>&1 || true
    fi
  done
}

apply_fix() {
  header

  log "${GREEN}Iniciando correção leve.${RESET}"
  log "Data: $(date)"
  log "Usuário: $USER"
  log "Diretório: $BASE_DIR"
  log "Backup: $BACKUP_DIR"

  run_cmd "Status antes da correção - sistema" systemctl --failed --no-pager
  run_cmd "Status antes da correção - usuário" systemctl --user --failed --no-pager

  title "Reinstalando/revalidando pacotes KDE e portal"
  log "${YELLOW}O pacman pode pedir confirmação.${RESET}"
  {
    echo "\$ sudo pacman -S --needed xdg-desktop-portal xdg-desktop-portal-kde xdg-desktop-portal-gtk plasma-workspace plasma-desktop kdeplasma-addons"
    sudo pacman -S --needed xdg-desktop-portal xdg-desktop-portal-kde xdg-desktop-portal-gtk plasma-workspace plasma-desktop kdeplasma-addons
  } >> "$LOG_FILE" 2>&1

  backup_and_move_cache

  run_shell "Reconstruindo cache KDE" '
    command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 --noincremental || true
  '

  run_shell "Reiniciando portals do usuário" '
    systemctl --user restart xdg-desktop-portal.service 2>/dev/null || true
    systemctl --user restart xdg-document-portal.service 2>/dev/null || true
  '

  run_shell "Status dos portals após correção" '
    systemctl --user status xdg-desktop-portal --no-pager || true
    echo
    systemctl --user status xdg-document-portal --no-pager || true
  '

  run_shell "Erros recentes relevantes após correção" '
    journalctl --user -b -p warning..alert --no-pager -n 200 | grep -Ei "xdg-desktop-portal|portal|plasmashell|mainscript|kde|dolphin" || true
  '

  echo
  log "${GREEN}Correção concluída.${RESET}"
  log "${YELLOW}Recomendado agora: sair da sessão KDE e entrar novamente, ou reiniciar o notebook.${RESET}"
  log "Log salvo em: $LOG_FILE"
  log "Backup salvo em: $BACKUP_DIR"

  pause
}

undo_fix() {
  header

  echo -e "${YELLOW}Este Undo só restaura caches movidos por uma execução anterior deste script.${RESET}"
  echo
  echo "Informe o caminho do backup criado pelo Apply."
  echo "Exemplo:"
  echo "$LOG_DIR/correcao-apps-lentos-kde-YYYYMMDD-HHMMSS/backup"
  echo
  read -rp "Caminho do backup: " SRC_BACKUP

  if [[ -z "${SRC_BACKUP// }" || ! -d "$SRC_BACKUP" ]]; then
    echo "Backup inválido ou não encontrado."
    pause
    return
  fi

  title "Restaurando caches do backup"

  shopt -s nullglob
  for item in "$SRC_BACKUP"/*; do
    base="$(basename "$item")"
    dest="$HOME/.cache/$base"

    if [[ -e "$dest" ]]; then
      mv "$dest" "$dest.pre-undo-$TS" >> "$LOG_FILE" 2>&1 || true
    fi

    mv "$item" "$dest" >> "$LOG_FILE" 2>&1 || true
    log "Restaurado: $dest"
  done
  shopt -u nullglob

  run_shell "Reconstruindo cache KDE após Undo" '
    command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 --noincremental || true
  '

  run_shell "Reiniciando portal após Undo" '
    systemctl --user restart xdg-desktop-portal.service 2>/dev/null || true
    systemctl --user restart xdg-document-portal.service 2>/dev/null || true
  '

  echo
  log "${GREEN}Undo concluído.${RESET}"
  log "${YELLOW}Recomendado: sair e entrar novamente na sessão KDE.${RESET}"

  pause
}

measure_app() {
  header

  echo "Informe o comando do app para medir."
  echo
  echo "Exemplos:"
  echo "  dolphin"
  echo "  systemsettings"
  echo "  flatpak run com.discordapp.Discord"
  echo
  read -rp "Comando: " APP_CMD

  if [[ -z "${APP_CMD// }" ]]; then
    echo "Comando vazio."
    pause
    return
  fi

  local TEST_LOG="$RUN_DIR/medicao-app-$TS.log"

  {
    echo "Medição do comando: $APP_CMD"
    echo "Data: $(date)"
    echo
  } | tee -a "$TEST_LOG"

  START_TS="$(date +%s)"

  timeout 20s bash -lc "$APP_CMD" >> "$TEST_LOG" 2>&1
  EXIT_CODE="$?"

  END_TS="$(date +%s)"
  ELAPSED="$((END_TS - START_TS))"

  {
    echo
    echo "Tempo aproximado do teste: ${ELAPSED}s"
    echo "Código de saída: $EXIT_CODE"

    if [[ "$EXIT_CODE" -eq 124 ]]; then
      echo "Observação: o app continuou aberto após 20s e foi encerrado pelo timeout."
    elif [[ "$EXIT_CODE" -eq 0 ]]; then
      echo "Observação: o comando terminou normalmente antes do timeout."
    else
      echo "Observação: o comando retornou erro."
    fi
  } >> "$TEST_LOG"

  echo
  echo -e "${GREEN}Resultado:${RESET}"
  cat "$TEST_LOG"

  pause
}

menu() {
  while true; do
    header
    echo "1) Apply    - aplicar correção leve"
    echo "2) Dry-run  - mostrar o que será feito"
    echo "3) Status   - verificar estado atual"
    echo "4) Undo     - restaurar caches de um backup anterior"
    echo "5) Medir abertura de app"
    echo "0) Sair"
    echo
    read -rp "Escolha uma opção: " opt

    case "$opt" in
      1) apply_fix ;;
      2) dry_run ;;
      3) status ;;
      4) undo_fix ;;
      5) measure_app ;;
      0) exit 0 ;;
      *) echo "Opção inválida."; sleep 1 ;;
    esac
  done
}

menu
