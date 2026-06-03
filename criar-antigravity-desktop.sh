#!/usr/bin/env bash
set -Eeuo pipefail

# ============================================================
# Antigravity IDE .desktop - CachyOS / GNOME
# Novo padrão de scripts
# ============================================================

APP_NAME="Antigravity IDE"
DESKTOP_ID="antigravity-ide.desktop"

CURRENT_USER="$(id -un)"
USER_HOME="${HOME:-/home/$CURRENT_USER}"

APP_EXEC="$USER_HOME/Antigravity IDE/antigravity-ide"
APP_ICON="$USER_HOME/Antigravity IDE/resources/app/resources/linux/code.png"

DESKTOP_DIR="$USER_HOME/.local/share/applications"
DESKTOP_FILE="$DESKTOP_DIR/$DESKTOP_ID"

SCRIPT_DIR="$(pwd)"
LOG_DIR="$SCRIPT_DIR/LOGS"
LOG_FILE="$LOG_DIR/criar-antigravity-desktop-$(date +%Y%m%d-%H%M%S).log"

mkdir -p "$LOG_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1

# Cores
RESET="\033[0m"
BOLD="\033[1m"
RED="\033[31m"
GREEN="\033[32m"
YELLOW="\033[33m"
BLUE="\033[34m"
CYAN="\033[36m"
GRAY="\033[90m"

print_header() {
  clear || true
  echo -e "${CYAN}============================================================${RESET}"
  echo -e "${BOLD}${APP_NAME} - Criador de .desktop para GNOME/CachyOS${RESET}"
  echo -e "${CYAN}============================================================${RESET}"
  echo -e "${GRAY}Usuário detectado:${RESET} $CURRENT_USER"
  echo -e "${GRAY}Home detectada:${RESET} $USER_HOME"
  echo -e "${GRAY}Executável:${RESET} $APP_EXEC"
  echo -e "${GRAY}Ícone:${RESET} $APP_ICON"
  echo -e "${GRAY}.desktop:${RESET} $DESKTOP_FILE"
  echo -e "${GRAY}Log:${RESET} $LOG_FILE"
  echo
}

pause() {
  echo
  read -rp "Pressione ENTER para continuar..."
}

ok() {
  echo -e "${GREEN}OK:${RESET} $*"
}

warn() {
  echo -e "${YELLOW}AVISO:${RESET} $*"
}

err() {
  echo -e "${RED}ERRO:${RESET} $*"
}

run_cmd() {
  local mode="$1"
  shift

  if [[ "$mode" == "dry-run" ]]; then
    echo -e "${YELLOW}[dry-run]${RESET} $*"
  else
    echo -e "${BLUE}[exec]${RESET} $*"
    "$@"
  fi
}

validate_paths() {
  local failed=0

  if [[ -f "$APP_EXEC" ]]; then
    ok "Executável encontrado."
  else
    err "Executável não encontrado:"
    echo "  $APP_EXEC"
    failed=1
  fi

  if [[ -f "$APP_ICON" ]]; then
    ok "Ícone encontrado."
  else
    err "Ícone não encontrado:"
    echo "  $APP_ICON"
    failed=1
  fi

  return "$failed"
}

show_status() {
  print_header

  echo -e "${BOLD}Status dos arquivos principais${RESET}"
  echo

  if [[ -f "$APP_EXEC" ]]; then
    if [[ -x "$APP_EXEC" ]]; then
      ok "Executável existe e está com permissão de execução."
    else
      warn "Executável existe, mas não está executável."
    fi
    echo "  $APP_EXEC"
  else
    err "Executável não encontrado."
    echo "  $APP_EXEC"
  fi

  echo

  if [[ -f "$APP_ICON" ]]; then
    ok "Ícone encontrado."
    echo "  $APP_ICON"
  else
    err "Ícone não encontrado."
    echo "  $APP_ICON"
  fi

  echo
  echo -e "${BOLD}Atalho local esperado${RESET}"
  echo

  if [[ -f "$DESKTOP_FILE" ]]; then
    ok ".desktop existe."
    echo "  $DESKTOP_FILE"
    echo
    echo -e "${BOLD}Conteúdo atual:${RESET}"
    echo -e "${GRAY}------------------------------------------------------------${RESET}"
    cat "$DESKTOP_FILE"
    echo -e "${GRAY}------------------------------------------------------------${RESET}"
  else
    warn ".desktop ainda não existe."
    echo "  $DESKTOP_FILE"
  fi

  echo
  echo -e "${BOLD}Possíveis atalhos relacionados encontrados${RESET}"
  echo

  find "$DESKTOP_DIR" /usr/share/applications \
    -maxdepth 1 \
    -type f \
    \( -iname '*antigravity*.desktop' -o -iname '*gravity*.desktop' \) \
    -print 2>/dev/null || true
}

write_desktop_file() {
  local mode="$1"

  if [[ "$mode" == "dry-run" ]]; then
    echo -e "${YELLOW}[dry-run] Criaria o arquivo:${RESET}"
    echo "  $DESKTOP_FILE"
    echo
    echo -e "${BOLD}Conteúdo que seria gravado:${RESET}"
    echo -e "${GRAY}------------------------------------------------------------${RESET}"
    cat <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Antigravity IDE
Comment=Google Antigravity IDE
Exec="$APP_EXEC" %F
Icon=$APP_ICON
Terminal=false
StartupNotify=true
StartupWMClass=Antigravity IDE
Categories=Development;IDE;
MimeType=text/plain;inode/directory;
Keywords=antigravity;google;ide;code;editor;ai;
EOF
    echo -e "${GRAY}------------------------------------------------------------${RESET}"
    return 0
  fi

  mkdir -p "$DESKTOP_DIR"

  if [[ -f "$DESKTOP_FILE" ]]; then
    local backup
    backup="$DESKTOP_FILE.bak.$(date +%Y%m%d-%H%M%S)"
    cp -a "$DESKTOP_FILE" "$backup"
    ok "Backup criado:"
    echo "  $backup"
  fi

  cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Antigravity IDE
Comment=Google Antigravity IDE
Exec="$APP_EXEC" %F
Icon=$APP_ICON
Terminal=false
StartupNotify=true
StartupWMClass=Antigravity IDE
Categories=Development;IDE;
MimeType=text/plain;inode/directory;
Keywords=antigravity;google;ide;code;editor;ai;
EOF

  chmod 644 "$DESKTOP_FILE"
  ok ".desktop criado/atualizado:"
  echo "  $DESKTOP_FILE"
}

disable_user_duplicates() {
  local mode="$1"

  echo
  echo -e "${BOLD}Verificando duplicados no diretório do usuário...${RESET}"

  mkdir -p "$DESKTOP_DIR"

  local duplicates
  duplicates="$(
    find "$DESKTOP_DIR" \
      -maxdepth 1 \
      -type f \
      \( -iname '*antigravity*.desktop' -o -iname '*gravity*.desktop' \) \
      ! -name "$DESKTOP_ID" \
      ! -name '*.disabled-by-script' \
      -print 2>/dev/null || true
  )"

  if [[ -z "$duplicates" ]]; then
    ok "Nenhum duplicado local encontrado."
    return 0
  fi

  echo "$duplicates"

  while IFS= read -r file; do
    [[ -z "$file" ]] && continue

    if [[ "$mode" == "dry-run" ]]; then
      echo -e "${YELLOW}[dry-run] Desativaria:${RESET} $file"
      echo -e "${YELLOW}[dry-run] Novo nome:${RESET} $file.disabled-by-script"
    else
      mv "$file" "$file.disabled-by-script"
      ok "Duplicado desativado:"
      echo "  $file.disabled-by-script"
    fi
  done <<< "$duplicates"
}

update_desktop_database_safe() {
  local mode="$1"

  echo
  echo -e "${BOLD}Atualizando banco de atalhos local...${RESET}"

  if ! command -v update-desktop-database >/dev/null 2>&1; then
    warn "update-desktop-database não encontrado."
    echo "Opcionalmente instale com:"
    echo "  sudo pacman -S desktop-file-utils"
    return 0
  fi

  run_cmd "$mode" update-desktop-database "$DESKTOP_DIR"
}

validate_desktop_file_safe() {
  local mode="$1"

  echo
  echo -e "${BOLD}Validando arquivo .desktop...${RESET}"

  if ! command -v desktop-file-validate >/dev/null 2>&1; then
    warn "desktop-file-validate não encontrado."
    echo "Opcionalmente instale com:"
    echo "  sudo pacman -S desktop-file-utils"
    return 0
  fi

  if [[ "$mode" == "dry-run" ]]; then
    echo -e "${YELLOW}[dry-run] Validaria:${RESET} $DESKTOP_FILE"
    return 0
  fi

  desktop-file-validate "$DESKTOP_FILE" || warn "Validação retornou avisos."
}

apply_changes() {
  local mode="${1:-apply}"

  print_header

  if [[ "$mode" == "dry-run" ]]; then
    echo -e "${YELLOW}${BOLD}Modo Dry-run: nenhuma alteração será feita.${RESET}"
  else
    echo -e "${GREEN}${BOLD}Modo Apply: alterações serão aplicadas.${RESET}"
  fi

  echo

  validate_paths || {
    err "Corrija os caminhos acima antes de aplicar."
    return 1
  }

  echo

  if [[ "$mode" == "dry-run" ]]; then
    echo -e "${YELLOW}[dry-run] Garantiria permissão de execução em:${RESET}"
    echo "  $APP_EXEC"
  else
    chmod +x "$APP_EXEC"
    ok "Permissão de execução garantida."
  fi

  echo

  write_desktop_file "$mode"
  disable_user_duplicates "$mode"
  validate_desktop_file_safe "$mode"
  update_desktop_database_safe "$mode"

  echo
  echo -e "${CYAN}============================================================${RESET}"

  if [[ "$mode" == "dry-run" ]]; then
    echo -e "${YELLOW}${BOLD}Dry-run concluído. Nenhuma alteração foi feita.${RESET}"
  else
    echo -e "${GREEN}${BOLD}Concluído.${RESET}"
    echo
    echo "Teste com:"
    echo "  gtk-launch antigravity-ide"
    echo
    echo "Ou procure no menu do GNOME por:"
    echo "  Antigravity IDE"
  fi

  echo -e "${CYAN}============================================================${RESET}"
}

undo_changes() {
  print_header

  echo -e "${YELLOW}${BOLD}Desfazendo alterações locais...${RESET}"
  echo

  if [[ -f "$DESKTOP_FILE" ]]; then
    rm -f "$DESKTOP_FILE"
    ok "Removido:"
    echo "  $DESKTOP_FILE"
  else
    warn ".desktop principal não existia:"
    echo "  $DESKTOP_FILE"
  fi

  echo
  echo -e "${BOLD}Restaurando atalhos desativados pelo script...${RESET}"

  mkdir -p "$DESKTOP_DIR"

  local disabled_files
  disabled_files="$(
    find "$DESKTOP_DIR" \
      -maxdepth 1 \
      -type f \
      -name '*.desktop.disabled-by-script' \
      -print 2>/dev/null || true
  )"

  if [[ -z "$disabled_files" ]]; then
    warn "Nenhum atalho desativado encontrado."
  else
    while IFS= read -r file; do
      [[ -z "$file" ]] && continue
      mv "$file" "${file%.disabled-by-script}"
      ok "Restaurado:"
      echo "  ${file%.disabled-by-script}"
    done <<< "$disabled_files"
  fi

  update_desktop_database_safe "apply"

  echo
  echo -e "${GREEN}${BOLD}Undo concluído.${RESET}"
}

show_menu() {
  while true; do
    print_header

    echo -e "${BOLD}Escolha uma opção:${RESET}"
    echo
    echo -e "  ${GREEN}1) Apply${RESET}   - Criar/corrigir o atalho"
    echo -e "  ${YELLOW}2) Dry-run${RESET} - Simular sem alterar nada"
    echo -e "  ${BLUE}3) Status${RESET}  - Mostrar situação atual"
    echo -e "  ${RED}4) Undo${RESET}    - Desfazer alterações locais"
    echo "  5) Sair"
    echo

    read -rp "Opção: " choice

    case "$choice" in
      1)
        apply_changes "apply"
        pause
        ;;
      2)
        apply_changes "dry-run"
        pause
        ;;
      3)
        show_status
        pause
        ;;
      4)
        undo_changes
        pause
        ;;
      5)
        echo
        echo "Saindo."
        echo "Log salvo em:"
        echo "$LOG_FILE"
        exit 0
        ;;
      *)
        echo
        warn "Opção inválida."
        pause
        ;;
    esac
  done
}

show_menu
