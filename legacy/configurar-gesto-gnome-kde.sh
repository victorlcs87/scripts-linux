#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_NAME="$(basename "$0")"
RUN_DIR="$(pwd)"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="$RUN_DIR/configurar-gesto-gnome-kde-$TIMESTAMP.log"

CONFIG_DIR="$HOME/.config"
GESTURES_CONF="$CONFIG_DIR/libinput-gestures.conf"
BACKUP_CONF="$CONFIG_DIR/libinput-gestures.conf.backup-$TIMESTAMP"

LOCAL_BIN="$HOME/.local/bin"
OVERVIEW_CMD="$LOCAL_BIN/kde-gnome-like-overview"

REQUIRED_AUR_PACKAGE="libinput-gestures"
REQUIRED_PACMAN_PACKAGES=("qt6-tools")

log() {
  echo -e "$*" | tee -a "$LOG_FILE"
}

run() {
  log ""
  log "\$ $*"
  "$@" 2>&1 | tee -a "$LOG_FILE"
}

die() {
  log ""
  log "ERRO: $*"
  log "Log salvo em: $LOG_FILE"
  exit 1
}

require_not_root() {
  if [[ "${EUID}" -eq 0 ]]; then
    die "Não execute este script como root. Execute como usuário normal. O script usará sudo quando necessário."
  fi
}

detect_session() {
  log "Sessão atual:"
  log "  XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-não definido}"
  log "  XDG_CURRENT_DESKTOP=${XDG_CURRENT_DESKTOP:-não definido}"
  log "  DESKTOP_SESSION=${DESKTOP_SESSION:-não definido}"

  if [[ "${XDG_SESSION_TYPE:-}" != "wayland" ]]; then
    log ""
    log "AVISO: sua sessão não parece ser Wayland."
    log "No KDE Plasma, este método é mais indicado para sessão Wayland."
  fi
}

install_pacman_packages() {
  local missing=()

  for pkg in "${REQUIRED_PACMAN_PACKAGES[@]}"; do
    if ! pacman -Q "$pkg" &>/dev/null; then
      missing+=("$pkg")
    fi
  done

  if (( ${#missing[@]} > 0 )); then
    log "Instalando dependências via pacman: ${missing[*]}"
    run sudo pacman -S --needed "${missing[@]}"
  else
    log "Dependências do pacman já instaladas."
  fi
}

ensure_aur_helper() {
  if command -v paru &>/dev/null; then
    echo "paru"
    return 0
  fi

  if command -v yay &>/dev/null; then
    echo "yay"
    return 0
  fi

  log "Nenhum helper AUR encontrado. Tentando instalar paru via pacman..."
  run sudo pacman -S --needed paru

  if command -v paru &>/dev/null; then
    echo "paru"
    return 0
  fi

  die "Não consegui encontrar ou instalar paru/yay. Instale um helper AUR e execute novamente."
}

install_libinput_gestures() {
  if command -v libinput-gestures-setup &>/dev/null; then
    log "libinput-gestures já está instalado."
    return 0
  fi

  local helper
  helper="$(ensure_aur_helper)"

  log "Instalando $REQUIRED_AUR_PACKAGE via $helper..."
  run "$helper" -S --needed "$REQUIRED_AUR_PACKAGE"

  if ! command -v libinput-gestures-setup &>/dev/null; then
    die "libinput-gestures foi instalado? Não encontrei o comando libinput-gestures-setup."
  fi
}

ensure_input_group() {
  if id -nG "$USER" | tr ' ' '\n' | grep -qx "input"; then
    log "Usuário '$USER' já pertence ao grupo input."
  else
    log "Adicionando usuário '$USER' ao grupo input..."
    run sudo gpasswd -a "$USER" input
    log ""
    log "IMPORTANTE: você precisa encerrar a sessão e entrar de novo, ou reiniciar, para o grupo input valer."
  fi
}

create_overview_helper() {
  mkdir -p "$LOCAL_BIN"

  if [[ -f "$OVERVIEW_CMD" ]]; then
    run cp -a "$OVERVIEW_CMD" "$OVERVIEW_CMD.backup-$TIMESTAMP"
  fi

  cat > "$OVERVIEW_CMD" <<'EOF'
#!/usr/bin/env bash

# Tenta abrir a Visão Geral / Overview do KDE Plasma.
# Mantém múltiplos fallbacks porque o nome interno pode variar entre versões do Plasma/KWin.

if command -v qdbus6 >/dev/null 2>&1; then
  qdbus6 org.kde.kglobalaccel /component/kwin org.kde.kglobalaccel.Component.invokeShortcut "Overview" >/dev/null 2>&1 && exit 0
  qdbus6 org.kde.KWin /Effects org.kde.kwin.Effects.toggleEffect "overview" >/dev/null 2>&1 && exit 0
  qdbus6 org.kde.KWin /Effects org.kde.kwin.Effects.toggleEffect "windowview" >/dev/null 2>&1 && exit 0
fi

if command -v qdbus >/dev/null 2>&1; then
  qdbus org.kde.kglobalaccel /component/kwin org.kde.kglobalaccel.Component.invokeShortcut "Overview" >/dev/null 2>&1 && exit 0
  qdbus org.kde.KWin /Effects org.kde.kwin.Effects.toggleEffect "overview" >/dev/null 2>&1 && exit 0
  qdbus org.kde.KWin /Effects org.kde.kwin.Effects.toggleEffect "windowview" >/dev/null 2>&1 && exit 0
fi

exit 1
EOF

  chmod +x "$OVERVIEW_CMD"
  log "Comando auxiliar criado em: $OVERVIEW_CMD"
}

configure_gesture() {
  mkdir -p "$CONFIG_DIR"

  if [[ -f "$GESTURES_CONF" ]]; then
    run cp -a "$GESTURES_CONF" "$BACKUP_CONF"
    log "Backup criado em: $BACKUP_CONF"
  fi

  cat > "$GESTURES_CONF" <<EOF
# Criado por $SCRIPT_NAME em $TIMESTAMP
# Objetivo:
#   3 dedos para cima no touchpad -> Visão Geral / Overview do KDE Plasma
#
# Para desfazer:
#   bash $SCRIPT_NAME undo

gesture swipe up 3 $OVERVIEW_CMD
EOF

  log "Configuração criada em: $GESTURES_CONF"
}

start_service() {
  log "Ativando autostart do libinput-gestures..."
  run libinput-gestures-setup autostart || true

  log "Reiniciando libinput-gestures..."
  run libinput-gestures-setup stop || true
  run libinput-gestures-setup start

  log ""
  log "Status:"
  run libinput-gestures-setup status || true
}

test_overview_command() {
  log ""
  log "Testando comando auxiliar da Visão Geral..."
  if "$OVERVIEW_CMD"; then
    log "Comando de Overview executado com sucesso."
  else
    log "AVISO: o comando auxiliar não conseguiu abrir a Overview agora."
    log "Isso pode acontecer se você ainda não reiniciou a sessão após entrar no grupo input ou se o KWin/DBus não expôs esse atalho."
  fi
}

apply() {
  require_not_root

  log "============================================================"
  log "Configurando gesto estilo GNOME no KDE Plasma"
  log "============================================================"
  log "Usuário: $USER"
  log "Home: $HOME"
  log "Diretório de execução: $RUN_DIR"
  log "Log: $LOG_FILE"
  log ""

  detect_session
  install_pacman_packages
  install_libinput_gestures
  ensure_input_group
  create_overview_helper
  configure_gesture
  start_service
  test_overview_command

  log ""
  log "============================================================"
  log "Concluído"
  log "============================================================"
  log "Teste agora:"
  log "  3 dedos para cima no touchpad"
  log ""
  log "Se não funcionar imediatamente, encerre a sessão e entre de novo."
  log "Se ainda não funcionar, execute:"
  log "  bash $SCRIPT_NAME status"
  log ""
  log "Log salvo em: $LOG_FILE"
}

undo() {
  require_not_root

  log "============================================================"
  log "Desfazendo gesto estilo GNOME no KDE Plasma"
  log "============================================================"
  log "Usuário: $USER"
  log "Diretório de execução: $RUN_DIR"
  log "Log: $LOG_FILE"
  log ""

  if command -v libinput-gestures-setup &>/dev/null; then
    run libinput-gestures-setup stop || true
    run libinput-gestures-setup autostop || true
  else
    log "libinput-gestures-setup não encontrado. Pulando parada do serviço."
  fi

  if [[ -f "$GESTURES_CONF" ]]; then
    run cp -a "$GESTURES_CONF" "$GESTURES_CONF.removido-$TIMESTAMP"
    run rm -f "$GESTURES_CONF"
    log "Configuração removida: $GESTURES_CONF"
  else
    log "Configuração não existe: $GESTURES_CONF"
  fi

  if [[ -f "$OVERVIEW_CMD" ]]; then
    run cp -a "$OVERVIEW_CMD" "$OVERVIEW_CMD.removido-$TIMESTAMP"
    run rm -f "$OVERVIEW_CMD"
    log "Comando auxiliar removido: $OVERVIEW_CMD"
  else
    log "Comando auxiliar não existe: $OVERVIEW_CMD"
  fi

  if id -nG "$USER" | tr ' ' '\n' | grep -qx "input"; then
    log ""
    log "O usuário '$USER' está no grupo input."
    log "Removendo do grupo input..."
    run sudo gpasswd -d "$USER" input || true
    log "IMPORTANTE: encerre a sessão e entre de novo, ou reinicie, para a remoção do grupo valer."
  else
    log "Usuário '$USER' não está no grupo input."
  fi

  log ""
  log "Desfeito."
  log "Log salvo em: $LOG_FILE"
}

status() {
  require_not_root

  log "============================================================"
  log "Status do gesto estilo GNOME no KDE Plasma"
  log "============================================================"
  log "Usuário: $USER"
  log "Log: $LOG_FILE"
  log ""

  detect_session

  log ""
  log "Comandos:"
  command -v libinput-gestures-setup &>/dev/null && log "  libinput-gestures-setup: OK" || log "  libinput-gestures-setup: NÃO ENCONTRADO"
  command -v qdbus6 &>/dev/null && log "  qdbus6: OK" || log "  qdbus6: NÃO ENCONTRADO"
  [[ -x "$OVERVIEW_CMD" ]] && log "  $OVERVIEW_CMD: OK" || log "  $OVERVIEW_CMD: NÃO ENCONTRADO"

  log ""
  log "Grupo input:"
  if id -nG "$USER" | tr ' ' '\n' | grep -qx "input"; then
    log "  Usuário '$USER' pertence ao grupo input."
  else
    log "  Usuário '$USER' NÃO pertence ao grupo input."
  fi

  log ""
  log "Arquivo de configuração:"
  if [[ -f "$GESTURES_CONF" ]]; then
    log "  Encontrado: $GESTURES_CONF"
    log ""
    log "Conteúdo:"
    sed 's/^/  /' "$GESTURES_CONF" | tee -a "$LOG_FILE"
  else
    log "  Não encontrado: $GESTURES_CONF"
  fi

  log ""
  if command -v libinput-gestures-setup &>/dev/null; then
    log "Status do libinput-gestures:"
    run libinput-gestures-setup status || true
  fi

  log ""
  log "Teste manual do Overview:"
  if [[ -x "$OVERVIEW_CMD" ]]; then
    if "$OVERVIEW_CMD"; then
      log "  Comando auxiliar executado com sucesso."
    else
      log "  Comando auxiliar falhou."
    fi
  fi

  log ""
  log "Log salvo em: $LOG_FILE"
}

case "${1:-}" in
  apply)
    apply
    ;;
  undo)
    undo
    ;;
  status)
    status
    ;;
  *)
    echo "Uso:"
    echo "  bash $SCRIPT_NAME apply"
    echo "  bash $SCRIPT_NAME status"
    echo "  bash $SCRIPT_NAME undo"
    exit 1
    ;;
esac
