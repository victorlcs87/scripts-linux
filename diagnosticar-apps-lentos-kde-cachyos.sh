#!/usr/bin/env bash

set -u

SCRIPT_NAME="$(basename "$0")"
BASE_DIR="$(pwd)"
LOG_DIR="$BASE_DIR/LOGS"
TS="$(date +%Y%m%d-%H%M%S)"
REPORT_DIR="$LOG_DIR/apps-lentos-diagnostico-$TS"
LOG_FILE="$REPORT_DIR/diagnostico.log"
ARCHIVE_FILE="$LOG_DIR/apps-lentos-diagnostico-$TS.tar.gz"

RED="\033[31m"
GREEN="\033[32m"
YELLOW="\033[33m"
BLUE="\033[34m"
MAGENTA="\033[35m"
CYAN="\033[36m"
BOLD="\033[1m"
RESET="\033[0m"

mkdir -p "$REPORT_DIR"

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
  local name="$1"
  shift

  title "$name"
  {
    echo "\$ $*"
    echo
    "$@"
  } >> "$LOG_FILE" 2>&1
}

run_shell() {
  local name="$1"
  local cmd="$2"

  title "$name"
  {
    echo "\$ $cmd"
    echo
    bash -lc "$cmd"
  } >> "$LOG_FILE" 2>&1
}

pause() {
  echo
  read -rp "Pressione ENTER para continuar..."
}

header() {
  clear
  echo -e "${BOLD}${MAGENTA}"
  echo "Diagnóstico de aplicativos lentos - CachyOS KDE"
  echo -e "${RESET}"
  echo -e "${CYAN}Logs:${RESET} $REPORT_DIR"
  echo
}

dry_run() {
  header
  log "${YELLOW}Modo Dry-run: nenhuma alteração será feita.${RESET}"
  echo
  echo "O modo Apply irá coletar:"
  echo
  echo "- Informações do sistema, kernel, sessão KDE/Wayland"
  echo "- Serviços systemd e systemd --user com falha"
  echo "- Tempo de boot e serviços lentos"
  echo "- Logs de erros do boot atual"
  echo "- Status do Baloo/indexação"
  echo "- Status de xdg-desktop-portal"
  echo "- Informações de Flatpak"
  echo "- Estado de montagens, fstab, discos e partições"
  echo "- Informações de GPU/NVIDIA/Mesa"
  echo "- Uso de CPU, RAM, swap e I/O"
  echo "- Pacotes relevantes instalados"
  echo
  echo "Nada será alterado no sistema."
  pause
}

status_quick() {
  header
  title "Status rápido"

  echo -e "${BOLD}Sessão:${RESET}"
  echo "XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-indefinido}"
  echo "XDG_CURRENT_DESKTOP=${XDG_CURRENT_DESKTOP:-indefinido}"
  echo "DESKTOP_SESSION=${DESKTOP_SESSION:-indefinido}"
  echo

  echo -e "${BOLD}Tempo de boot:${RESET}"
  systemd-analyze 2>/dev/null || true
  echo

  echo -e "${BOLD}Serviços do sistema com falha:${RESET}"
  systemctl --failed --no-pager 2>/dev/null || true
  echo

  echo -e "${BOLD}Serviços do usuário com falha:${RESET}"
  systemctl --user --failed --no-pager 2>/dev/null || true
  echo

  echo -e "${BOLD}Baloo:${RESET}"
  if command -v balooctl6 >/dev/null 2>&1; then
    balooctl6 status 2>/dev/null || true
  elif command -v balooctl >/dev/null 2>&1; then
    balooctl status 2>/dev/null || true
  else
    echo "balooctl não encontrado."
  fi
  echo

  echo -e "${BOLD}Portal KDE/Flatpak:${RESET}"
  systemctl --user --no-pager --type=service | grep -Ei 'xdg-desktop-portal|portal|flatpak' || true
  echo

  echo -e "${BOLD}Uso atual:${RESET}"
  free -h
  echo
  df -hT | sed -n '1,15p'
  echo

  pause
}

collect_apply() {
  header
  log "${GREEN}Iniciando diagnóstico completo.${RESET}"
  log "Data: $(date)"
  log "Usuário: $USER"
  log "Diretório de execução: $BASE_DIR"
  log "Relatório: $REPORT_DIR"

  run_cmd "Sistema" hostnamectl
  run_cmd "Kernel" uname -a
  run_cmd "Uptime" uptime
  run_cmd "Sessão atual" printenv

  run_shell "Variáveis relevantes da sessão" '
    echo "XDG_SESSION_TYPE=$XDG_SESSION_TYPE"
    echo "XDG_CURRENT_DESKTOP=$XDG_CURRENT_DESKTOP"
    echo "DESKTOP_SESSION=$DESKTOP_SESSION"
    echo "KDE_SESSION_VERSION=$KDE_SESSION_VERSION"
    echo "QT_QPA_PLATFORM=$QT_QPA_PLATFORM"
    echo "WAYLAND_DISPLAY=$WAYLAND_DISPLAY"
    echo "DISPLAY=$DISPLAY"
  '

  run_shell "Versões KDE/Plasma" '
    command -v plasmashell >/dev/null && plasmashell --version || true
    command -v kinfo >/dev/null && kinfo || true
    command -v kwin_wayland >/dev/null && kwin_wayland --version || true
  '

  run_cmd "Tempo de boot" systemd-analyze
  run_cmd "Serviços mais lentos no boot" systemd-analyze blame
  run_cmd "Cadeia crítica do boot" systemd-analyze critical-chain

  run_cmd "Serviços do sistema com falha" systemctl --failed --no-pager
  run_cmd "Serviços do usuário com falha" systemctl --user --failed --no-pager

  run_shell "Jobs pendentes do systemd" '
    systemctl list-jobs --no-pager || true
    echo
    systemctl --user list-jobs --no-pager || true
  '

  run_cmd "Logs de avisos/erros do boot atual" journalctl -b -p warning..alert --no-pager -n 500
  run_cmd "Logs de avisos/erros do usuário no boot atual" journalctl --user -b -p warning..alert --no-pager -n 500

  run_shell "Status xdg-desktop-portal" '
    systemctl --user status xdg-desktop-portal --no-pager || true
    echo
    systemctl --user status xdg-desktop-portal-kde --no-pager || true
    echo
    systemctl --user status xdg-document-portal --no-pager || true
  '

  run_shell "Logs xdg-desktop-portal" '
    journalctl --user -b -u xdg-desktop-portal --no-pager -n 250 || true
    echo
    journalctl --user -b -u xdg-desktop-portal-kde --no-pager -n 250 || true
  '

  run_shell "Baloo indexação KDE" '
    if command -v balooctl6 >/dev/null 2>&1; then
      balooctl6 status || true
    elif command -v balooctl >/dev/null 2>&1; then
      balooctl status || true
    else
      echo "balooctl não encontrado."
    fi
  '

  run_shell "Processos consumindo CPU e RAM" '
    echo "TOP CPU:"
    ps -eo pid,ppid,comm,%cpu,%mem,etime,args --sort=-%cpu | head -40
    echo
    echo "TOP MEM:"
    ps -eo pid,ppid,comm,%cpu,%mem,etime,args --sort=-%mem | head -40
  '

  run_cmd "Memória" free -h
  run_cmd "Swap" swapon --show
  run_cmd "Discos" lsblk -o NAME,SIZE,FSTYPE,LABEL,UUID,FSAVAIL,FSUSE%,MOUNTPOINTS
  run_cmd "Uso dos sistemas de arquivos" df -hT
  run_cmd "Montagens ativas" findmnt
  run_cmd "fstab" cat /etc/fstab

  run_shell "Verificação do fstab" '
    findmnt --verify --verbose || true
  '

  run_shell "NetworkManager e DNS" '
    command -v nmcli >/dev/null && nmcli general status || true
    echo
    command -v resolvectl >/dev/null && resolvectl status || true
  '

  run_shell "GPU / NVIDIA / Mesa" '
    command -v nvidia-smi >/dev/null && nvidia-smi || true
    echo
    command -v glxinfo >/dev/null && glxinfo -B || true
    echo
    command -v vulkaninfo >/dev/null && vulkaninfo --summary || true
  '

  run_shell "Flatpak" '
    command -v flatpak >/dev/null && flatpak --version || true
    echo
    command -v flatpak >/dev/null && flatpak list --app --columns=application,origin,installation || true
    echo
    command -v flatpak >/dev/null && flatpak repair --dry-run || true
  '

  run_shell "Pacotes relevantes instalados" '
    pacman -Q 2>/dev/null | grep -Ei "nvidia|mesa|plasma|kde|xdg-desktop-portal|flatpak|baloo|systemd|steam|firefox|font|fuse|rclone|ntfs|power|tuned|gamemode|mangohud" || true
  '

  run_shell "Serviços relacionados a montagem/rclone/fuse" '
    systemctl --user --no-pager --type=service | grep -Ei "rclone|fuse|mount|gdrive|google" || true
    echo
    systemctl --no-pager --type=service | grep -Ei "mount|ntfs|fuse|rclone" || true
  '

  run_shell "Caches do usuário potencialmente relevantes" '
    du -sh "$HOME/.cache" 2>/dev/null || true
    du -sh "$HOME/.cache/fontconfig" 2>/dev/null || true
    du -sh "$HOME/.cache/mesa_shader_cache" 2>/dev/null || true
    du -sh "$HOME/.cache/nvidia" 2>/dev/null || true
    du -sh "$HOME/.var/app" 2>/dev/null || true
  '

  title "Resumo automático de possíveis pontos de atenção"

  {
    echo
    echo "Serviços com falha:"
    systemctl --failed --no-pager 2>/dev/null | sed -n '1,30p'
    echo
    systemctl --user --failed --no-pager 2>/dev/null | sed -n '1,30p'
    echo
    echo "Erros recentes relacionados a portal, kde, plasma, mount, nvidia:"
    journalctl -b -p warning..alert --no-pager -n 1000 2>/dev/null | grep -Ei "xdg-desktop-portal|portal|plasma|kwin|kde|mount|fstab|nvidia|flatpak|baloo|timeout|failed" | tail -120 || true
    echo
  } >> "$LOG_FILE" 2>&1

  tar -czf "$ARCHIVE_FILE" -C "$LOG_DIR" "$(basename "$REPORT_DIR")" 2>/dev/null || true

  echo
  log "${GREEN}Diagnóstico concluído.${RESET}"
  log "Log principal: $LOG_FILE"
  log "Arquivo compactado: $ARCHIVE_FILE"
  echo
  echo -e "${YELLOW}Me envie o arquivo .tar.gz ou cole o resumo do log para eu analisar.${RESET}"
  pause
}

undo_noop() {
  header
  echo -e "${YELLOW}Undo não é necessário.${RESET}"
  echo
  echo "Este script não altera configurações, pacotes, serviços ou arquivos do sistema."
  echo "Ele apenas coleta informações e salva logs em:"
  echo
  echo "$LOG_DIR"
  pause
}

measure_app() {
  header
  echo -e "${CYAN}Medição simples de abertura de aplicativo${RESET}"
  echo
  echo "Informe um comando para testar."
  echo "Exemplos:"
  echo "  dolphin"
  echo "  systemsettings"
  echo "  firefox"
  echo "  flatpak run org.mozilla.firefox"
  echo
  read -rp "Comando: " APP_CMD

  if [[ -z "${APP_CMD// }" ]]; then
    echo "Comando vazio."
    pause
    return
  fi

  local TEST_LOG="$REPORT_DIR/medicao-app-$TS.log"
  echo "Medição do comando: $APP_CMD" | tee -a "$TEST_LOG"
  echo "Data: $(date)" | tee -a "$TEST_LOG"
  echo | tee -a "$TEST_LOG"

  echo -e "${YELLOW}O app será aberto e o script tentará encerrar depois de 15 segundos.${RESET}"
  echo "Isso é apenas uma medição aproximada."
  echo

  START_TS="$(date +%s)"

  timeout 15s bash -lc "$APP_CMD" >> "$TEST_LOG" 2>&1
  EXIT_CODE="$?"

  END_TS="$(date +%s)"
  ELAPSED="$((END_TS - START_TS))"

  {
    echo
    echo "Tempo aproximado do teste: ${ELAPSED}s"
    echo "Código de saída: $EXIT_CODE"

    if [[ "$EXIT_CODE" -eq 124 ]]; then
      echo "Observação: o aplicativo continuou aberto após 15s e foi encerrado pelo timeout."
      echo "Isso geralmente significa que o app abriu e permaneceu rodando normalmente."
    elif [[ "$EXIT_CODE" -eq 0 ]]; then
      echo "Observação: o comando terminou normalmente antes do timeout."
    else
      echo "Observação: o comando retornou erro. Veja as mensagens acima."
    fi
  } >> "$TEST_LOG"

  echo
  echo -e "${GREEN}Resultado salvo em:${RESET} $TEST_LOG"
  cat "$TEST_LOG"
  pause
}

menu() {
  while true; do
    header
    echo "1) Apply    - coletar diagnóstico completo"
    echo "2) Dry-run  - mostrar o que seria coletado"
    echo "3) Status   - status rápido"
    echo "4) Undo     - explicar reversão"
    echo "5) Medir tempo de abertura de um app"
    echo "0) Sair"
    echo
    read -rp "Escolha uma opção: " opt

    case "$opt" in
      1) collect_apply ;;
      2) dry_run ;;
      3) status_quick ;;
      4) undo_noop ;;
      5) measure_app ;;
      0) exit 0 ;;
      *) echo "Opção inválida."; sleep 1 ;;
    esac
  done
}

menu
