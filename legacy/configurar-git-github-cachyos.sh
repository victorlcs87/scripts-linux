#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$(pwd)"
LOG_FILE="$RUN_DIR/configurar-git-github-$(date +%Y%m%d-%H%M%S).log"

SSH_KEY="$HOME/.ssh/id_ed25519_github"
SSH_CONFIG="$HOME/.ssh/config"
BACKUP_DIR="$HOME/.config/git-github-cachyos-backup"
MARKER_BEGIN="# >>> git-github-cachyos >>>"
MARKER_END="# <<< git-github-cachyos <<<"

log() {
  echo -e "$*" | tee -a "$LOG_FILE"
}

run_cmd() {
  local dry_run="$1"
  shift

  log ""
  log "$ $*"

  if [[ "$dry_run" == "true" ]]; then
    return 0
  fi

  "$@" 2>&1 | tee -a "$LOG_FILE"
}

pause() {
  echo
  read -r -p "Pressione ENTER para continuar..."
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

install_deps() {
  local dry_run="$1"

  log "Verificando dependências..."

  local pkgs=()
  need_cmd git || pkgs+=("git")
  need_cmd ssh || pkgs+=("openssh")
  need_cmd gh || pkgs+=("github-cli")
  need_cmd wl-copy || pkgs+=("wl-clipboard")

  if [[ "${#pkgs[@]}" -eq 0 ]]; then
    log "Dependências já instaladas."
    return 0
  fi

  log "Dependências ausentes: ${pkgs[*]}"
  run_cmd "$dry_run" sudo pacman -S --needed "${pkgs[@]}"
}

show_status() {
  log "============================================================"
  log "Status Git/GitHub"
  log "============================================================"
  log "Log: $LOG_FILE"
  log ""

  log "Git:"
  if need_cmd git; then
    git --version 2>&1 | tee -a "$LOG_FILE" || true
    log "user.name:  $(git config --global --get user.name || echo '<não configurado>')"
    log "user.email: $(git config --global --get user.email || echo '<não configurado>')"
    log "defaultBranch: $(git config --global --get init.defaultBranch || echo '<não configurado>')"
    log "pull.rebase:   $(git config --global --get pull.rebase || echo '<não configurado>')"
  else
    log "git não instalado."
  fi

  log ""
  log "SSH:"
  if [[ -f "$SSH_KEY" ]]; then
    log "Chave privada encontrada: $SSH_KEY"
  else
    log "Chave privada não encontrada: $SSH_KEY"
  fi

  if [[ -f "$SSH_KEY.pub" ]]; then
    log "Chave pública encontrada: $SSH_KEY.pub"
    log "Fingerprint:"
    ssh-keygen -lf "$SSH_KEY.pub" 2>&1 | tee -a "$LOG_FILE" || true
  else
    log "Chave pública não encontrada: $SSH_KEY.pub"
  fi

  log ""
  log "Arquivo SSH config:"
  if [[ -f "$SSH_CONFIG" ]]; then
    if grep -qF "$MARKER_BEGIN" "$SSH_CONFIG"; then
      log "Bloco github.com criado pelo script encontrado em $SSH_CONFIG."
    else
      log "$SSH_CONFIG existe, mas sem bloco criado por este script."
    fi
  else
    log "$SSH_CONFIG não existe."
  fi

  log ""
  log "Teste SSH GitHub:"
  if need_cmd ssh; then
    ssh -T git@github.com 2>&1 | tee -a "$LOG_FILE" || true
  else
    log "ssh não instalado."
  fi

  log ""
  log "GitHub CLI:"
  if need_cmd gh; then
    gh auth status 2>&1 | tee -a "$LOG_FILE" || true
  else
    log "gh não instalado."
  fi
}

apply_config() {
  local dry_run="$1"

  log "============================================================"
  log "Configurando Git + GitHub SSH no CachyOS"
  log "============================================================"
  log "Log: $LOG_FILE"
  log "Dry-run: $dry_run"
  log ""

  install_deps "$dry_run"

  echo
  read -r -p "Nome para commits Git [Victor Lima]: " GIT_NAME
  GIT_NAME="${GIT_NAME:-Victor Lima}"

  read -r -p "E-mail do GitHub para commits: " GIT_EMAIL
  if [[ -z "${GIT_EMAIL// }" ]]; then
    log "ERRO: e-mail não informado."
    return 1
  fi

  mkdir -p "$BACKUP_DIR"

  if [[ "$dry_run" == "false" ]]; then
    git config --global user.name "$GIT_NAME"
    git config --global user.email "$GIT_EMAIL"
    git config --global init.defaultBranch main
    git config --global pull.rebase false
    git config --global core.editor nano
  else
    log "DRY-RUN: configuraria git user.name=$GIT_NAME"
    log "DRY-RUN: configuraria git user.email=$GIT_EMAIL"
    log "DRY-RUN: configuraria init.defaultBranch=main"
    log "DRY-RUN: configuraria pull.rebase=false"
    log "DRY-RUN: configuraria core.editor=nano"
  fi

  if [[ "$dry_run" == "false" ]]; then
    mkdir -p "$HOME/.ssh"
    chmod 700 "$HOME/.ssh"
  else
    log "DRY-RUN: criaria/ajustaria $HOME/.ssh com chmod 700"
  fi

  if [[ -f "$SSH_KEY" ]]; then
    log "Chave já existe: $SSH_KEY"
  else
    run_cmd "$dry_run" ssh-keygen -t ed25519 -C "$GIT_EMAIL" -f "$SSH_KEY"
  fi

  if [[ "$dry_run" == "false" ]]; then
    chmod 600 "$SSH_KEY" 2>/dev/null || true
    chmod 644 "$SSH_KEY.pub" 2>/dev/null || true
  fi

  if [[ -f "$SSH_CONFIG" && "$dry_run" == "false" ]]; then
    cp -a "$SSH_CONFIG" "$BACKUP_DIR/config.backup.$(date +%Y%m%d-%H%M%S)"
  fi

  if [[ "$dry_run" == "false" ]]; then
    touch "$SSH_CONFIG"
    chmod 600 "$SSH_CONFIG"

    if grep -qF "$MARKER_BEGIN" "$SSH_CONFIG"; then
      log "Bloco do script já existe em $SSH_CONFIG. Mantendo."
    else
      cat >> "$SSH_CONFIG" <<EOF

$MARKER_BEGIN
Host github.com
  HostName github.com
  User git
  IdentityFile $SSH_KEY
  IdentitiesOnly yes
$MARKER_END
EOF
      log "Bloco SSH adicionado em $SSH_CONFIG."
    fi
  else
    log "DRY-RUN: adicionaria bloco Host github.com em $SSH_CONFIG"
  fi

  log ""
  log "Adicionando chave ao ssh-agent da sessão atual..."
  if [[ "$dry_run" == "false" ]]; then
    eval "$(ssh-agent -s)" 2>&1 | tee -a "$LOG_FILE" || true
    ssh-add "$SSH_KEY" 2>&1 | tee -a "$LOG_FILE" || true
  else
    log "DRY-RUN: executaria ssh-agent e ssh-add $SSH_KEY"
  fi

  log ""
  log "Chave pública:"
  if [[ "$dry_run" == "false" && -f "$SSH_KEY.pub" ]]; then
    cat "$SSH_KEY.pub" | tee -a "$LOG_FILE"

    if need_cmd wl-copy; then
      wl-copy < "$SSH_KEY.pub"
      log ""
      log "Chave pública copiada para a área de transferência."
    fi
  else
    log "DRY-RUN: exibiria/copiaría $SSH_KEY.pub"
  fi

  log ""
  log "Agora você tem duas opções:"
  log "1) Abrir GitHub > Settings > SSH and GPG keys > New SSH key e colar a chave."
  log "2) Usar a CLI: gh auth login e depois gh ssh-key add \"$SSH_KEY.pub\" --title \"Alienware CachyOS\""

  echo
  read -r -p "Deseja tentar adicionar a chave pelo GitHub CLI agora? [s/N]: " ADD_GH
  ADD_GH="${ADD_GH:-N}"

  if [[ "$ADD_GH" =~ ^[sS]$ ]]; then
    if [[ "$dry_run" == "true" ]]; then
      log "DRY-RUN: executaria gh auth login e gh ssh-key add"
    else
      gh auth status >/dev/null 2>&1 || gh auth login
      gh ssh-key add "$SSH_KEY.pub" --title "Alienware CachyOS" --type authentication 2>&1 | tee -a "$LOG_FILE" || true
    fi
  fi

  log ""
  log "Teste final:"
  if [[ "$dry_run" == "false" ]]; then
    ssh -T git@github.com 2>&1 | tee -a "$LOG_FILE" || true
  else
    log "DRY-RUN: executaria ssh -T git@github.com"
  fi

  log ""
  log "Concluído. Log salvo em: $LOG_FILE"
}

undo_config() {
  log "============================================================"
  log "Undo Git + GitHub SSH"
  log "============================================================"
  log "Log: $LOG_FILE"
  log ""

  log "Isso vai remover as configurações globais básicas do Git criadas pelo script"
  log "e remover o bloco github.com marcado no ~/.ssh/config."
  log "A chave SSH NÃO será apagada automaticamente."
  echo
  read -r -p "Digite DESFAZER-GIT-GITHUB para continuar: " CONFIRM

  if [[ "$CONFIRM" != "DESFAZER-GIT-GITHUB" ]]; then
    log "Operação cancelada."
    return 0
  fi

  if need_cmd git; then
    git config --global --unset user.name 2>/dev/null || true
    git config --global --unset user.email 2>/dev/null || true
    git config --global --unset init.defaultBranch 2>/dev/null || true
    git config --global --unset pull.rebase 2>/dev/null || true
    git config --global --unset core.editor 2>/dev/null || true
    log "Configurações globais removidas quando existiam."
  fi

  if [[ -f "$SSH_CONFIG" ]]; then
    mkdir -p "$BACKUP_DIR"
    cp -a "$SSH_CONFIG" "$BACKUP_DIR/config.before-undo.$(date +%Y%m%d-%H%M%S)"

    awk -v begin="$MARKER_BEGIN" -v end="$MARKER_END" '
      $0 == begin {skip=1; next}
      $0 == end {skip=0; next}
      skip != 1 {print}
    ' "$SSH_CONFIG" > "$SSH_CONFIG.tmp"

    mv "$SSH_CONFIG.tmp" "$SSH_CONFIG"
    chmod 600 "$SSH_CONFIG"
    log "Bloco SSH removido de $SSH_CONFIG."
  fi

  echo
  read -r -p "Deseja apagar também a chave $SSH_KEY e $SSH_KEY.pub? [s/N]: " DELKEY
  DELKEY="${DELKEY:-N}"

  if [[ "$DELKEY" =~ ^[sS]$ ]]; then
    rm -f "$SSH_KEY" "$SSH_KEY.pub"
    log "Chave SSH apagada."
  else
    log "Chave SSH preservada."
  fi

  log "Undo concluído. Log salvo em: $LOG_FILE"
}

while true; do
  clear
  echo "============================================================"
  echo "Git + GitHub SSH - CachyOS"
  echo "============================================================"
  echo "1) Apply"
  echo "2) Dry-run"
  echo "3) Status"
  echo "4) Undo"
  echo "5) Sair"
  echo
  read -r -p "Escolha uma opção: " opt

  case "$opt" in
    1) apply_config "false"; pause ;;
    2) apply_config "true"; pause ;;
    3) show_status; pause ;;
    4) undo_config; pause ;;
    5) exit 0 ;;
    *) echo "Opção inválida."; sleep 1 ;;
  esac
done
