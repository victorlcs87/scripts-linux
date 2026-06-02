#!/usr/bin/env bash
set -Eeuo pipefail

# ============================================================
# Configurar montagens fixas no /etc/fstab - CachyOS/KDE/GNOME
# Padrão Victor:
# - Menu interativo
# - Apply / Dry-run / Status / Undo / Sair
# - Log na pasta onde o script foi executado
# - Nada executado como root diretamente; usa sudo quando necessário
# ============================================================

SCRIPT_NAME="$(basename "$0")"
EXEC_DIR="$(pwd)"
LOG_FILE="$EXEC_DIR/${SCRIPT_NAME%.sh}-$(date +%Y%m%d-%H%M%S).log"

FSTAB="/etc/fstab"
MARK_BEGIN="# >>> VICTOR-FSTAB-AUTOMOUNT-BEGIN"
MARK_END="# <<< VICTOR-FSTAB-AUTOMOUNT-END"

TARGET_USER="${SUDO_USER:-$USER}"
TARGET_UID="$(id -u "$TARGET_USER")"
TARGET_GID="$(id -g "$TARGET_USER")"

DRY_RUN=1

# ------------------------------------------------------------
# Dispositivos que serão montados automaticamente
# ------------------------------------------------------------
# Formato:
# UUID|MOUNTPOINT|FSTYPE|OPTIONS|PASSNO|DESCRIPTION

ENTRIES=(
  "3620968B209651AB|/mnt/windows|ntfs3|rw,nofail,x-systemd.automount,x-systemd.idle-timeout=10min,x-systemd.device-timeout=10,uid=$TARGET_UID,gid=$TARGET_GID,umask=022,windows_names,prealloc,noatime|0|WINDOWS"
  "A234FA5C34FA3341|/mnt/dados-windows|ntfs3|rw,nofail,x-systemd.automount,x-systemd.idle-timeout=10min,x-systemd.device-timeout=10,uid=$TARGET_UID,gid=$TARGET_GID,umask=022,windows_names,prealloc,noatime|0|DADOS WINDOWS"
  "71944da5-b86c-4a0c-8973-c89a2a6e873f|/mnt/jogos-linux|ext4|defaults,nofail,x-systemd.automount,x-systemd.idle-timeout=10min,x-systemd.device-timeout=10,noatime,commit=60|2|JOGOS LINUX"
)

# ------------------------------------------------------------
# Dispositivos conhecidos que NÃO serão montados automaticamente
# ------------------------------------------------------------

IGNORED_NOTES=(
  "7894-893D|nvme0n1p1|vfat/FAT32|Provável EFI. Não montar automaticamente."
  "A0D8BC16D8BBE924|nvme0n1p4|ntfs sem label|Pode ser Recovery/partição do Windows. Não montar automaticamente."
)

# ------------------------------------------------------------
# Logging
# ------------------------------------------------------------

mkdir -p "$EXEC_DIR"
touch "$LOG_FILE"

exec > >(tee -a "$LOG_FILE") 2>&1

print_header() {
  clear || true
  echo "============================================================"
  echo "Configurar fstab automount - CachyOS"
  echo "============================================================"
  echo "Usuário alvo: $TARGET_USER"
  echo "UID:GID: $TARGET_UID:$TARGET_GID"
  echo "Log: $LOG_FILE"
  echo
}

pause() {
  echo
  read -rp "Pressione Enter para continuar..."
}

run() {
  echo "+ $*"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    "$@"
  fi
}

sudo_run() {
  echo "+ sudo $*"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    sudo "$@"
  fi
}

sudo_write_file() {
  local target="$1"
  local source="$2"

  echo "+ sudo install -m 0644 $source $target"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    sudo install -m 0644 "$source" "$target"
  fi
}

require_commands() {
  local missing=0
  local commands=(
    blkid
    findmnt
    lsblk
    awk
    sed
    mktemp
    systemctl
    mount
    umount
  )

  echo "Verificando comandos necessários..."

  for cmd in "${commands[@]}"; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      echo "ERRO: comando não encontrado: $cmd"
      missing=1
    else
      echo "OK: $cmd"
    fi
  done

  if [[ "$missing" -eq 1 ]]; then
    echo
    echo "Instale os comandos ausentes antes de continuar."
    exit 1
  fi
}

check_ntfs3_support() {
  echo
  echo "Verificando suporte ao ntfs3..."

  if grep -qw ntfs3 /proc/filesystems; then
    echo "OK: ntfs3 disponível no kernel atual."
    return 0
  fi

  if modinfo ntfs3 >/dev/null 2>&1; then
    echo "OK: módulo ntfs3 existe. Tentando carregar quando necessário."
    if [[ "$DRY_RUN" -eq 0 ]]; then
      sudo modprobe ntfs3 || true
    fi
    return 0
  fi

  echo "AVISO: ntfs3 não parece disponível."
  echo "Se o mount falhar, instale/ative suporte NTFS ou ajuste para ntfs-3g."
}

show_current_disks() {
  echo
  echo "Discos e partições atuais:"
  echo "------------------------------------------------------------"
  lsblk -f
  echo "------------------------------------------------------------"
}

get_device_by_uuid() {
  local uuid="$1"
  blkid -U "$uuid" 2>/dev/null || true
}

verify_uuids() {
  echo
  echo "Verificando UUIDs configurados..."

  local missing=0

  for entry in "${ENTRIES[@]}"; do
    IFS="|" read -r uuid mountpoint fstype options passno description <<< "$entry"

    local dev
    dev="$(get_device_by_uuid "$uuid")"

    if [[ -z "$dev" ]]; then
      echo "ERRO: UUID não encontrado: $uuid ($description)"
      missing=1
    else
      echo "OK: $description"
      echo "  UUID: $uuid"
      echo "  Device: $dev"
      echo "  Mountpoint futuro: $mountpoint"
    fi
  done

  if [[ "$missing" -eq 1 ]]; then
    echo
    echo "Abortando para evitar quebrar o fstab."
    exit 1
  fi
}

backup_fstab() {
  local backup_dir="$EXEC_DIR/fstab-backups"
  local backup_file="$backup_dir/fstab.$(date +%Y%m%d-%H%M%S).bak"

  echo
  echo "Criando backup do /etc/fstab..."
  run mkdir -p "$backup_dir"

  echo "+ sudo cp -a $FSTAB $backup_file"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    sudo cp -a "$FSTAB" "$backup_file"
    sudo chown "$TARGET_UID:$TARGET_GID" "$backup_file" || true
  fi

  echo "Backup planejado/criado em:"
  echo "  $backup_file"
}

build_new_fstab_without_block() {
  local source_file="$1"
  local output_file="$2"

  awk -v begin="$MARK_BEGIN" -v end="$MARK_END" '
    $0 == begin {skip=1; next}
    $0 == end {skip=0; next}
    skip != 1 {print}
  ' "$source_file" > "$output_file"
}

build_fstab_block() {
  local output_file="$1"

  {
    echo
    echo "$MARK_BEGIN"
    echo "# Montagens fixas geradas por $SCRIPT_NAME"
    echo "# Data: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "# Usuário: $TARGET_USER"
    echo "#"
    echo "# Opções usadas:"
    echo "# - nofail: evita travar o boot se a partição falhar."
    echo "# - x-systemd.automount: monta sob demanda."
    echo "# - x-systemd.idle-timeout=10min: desmonta após inatividade."
    echo "# - noatime: reduz escritas no NVMe."
    echo "# - commit=60 em ext4: reduz frequência de commits em disco."
    echo "#"
    echo "# Partições propositalmente ignoradas:"
    for note in "${IGNORED_NOTES[@]}"; do
      IFS="|" read -r uuid dev info reason <<< "$note"
      echo "# - $dev / UUID=$uuid / $info / $reason"
    done
    echo

    for entry in "${ENTRIES[@]}"; do
      IFS="|" read -r uuid mountpoint fstype options passno description <<< "$entry"
      echo "# $description"
      echo "UUID=$uuid $mountpoint $fstype $options 0 $passno"
      echo
    done

    echo "$MARK_END"
  } > "$output_file"
}

preview_new_fstab() {
  local tmp_base
  local tmp_block
  local tmp_final

  tmp_base="$(mktemp)"
  tmp_block="$(mktemp)"
  tmp_final="$(mktemp)"

  build_new_fstab_without_block "$FSTAB" "$tmp_base"
  build_fstab_block "$tmp_block"

  cat "$tmp_base" "$tmp_block" > "$tmp_final"

  echo
  echo "Prévia do bloco que será adicionado ao /etc/fstab:"
  echo "------------------------------------------------------------"
  cat "$tmp_block"
  echo "------------------------------------------------------------"

  rm -f "$tmp_base" "$tmp_block" "$tmp_final"
}

apply_fstab_changes() {
  local tmp_base
  local tmp_block
  local tmp_final

  tmp_base="$(mktemp)"
  tmp_block="$(mktemp)"
  tmp_final="$(mktemp)"

  build_new_fstab_without_block "$FSTAB" "$tmp_base"
  build_fstab_block "$tmp_block"
  cat "$tmp_base" "$tmp_block" > "$tmp_final"

  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo
    echo "Dry-run: o /etc/fstab seria reescrito removendo bloco antigo e adicionando o novo bloco."
    echo
    echo "Bloco novo:"
    echo "------------------------------------------------------------"
    cat "$tmp_block"
    echo "------------------------------------------------------------"
  else
    echo
    echo "Aplicando novo /etc/fstab..."
    sudo_write_file "$FSTAB" "$tmp_final"
  fi

  rm -f "$tmp_base" "$tmp_block" "$tmp_final"
}

create_mountpoints() {
  echo
  echo "Criando pontos de montagem..."

  for entry in "${ENTRIES[@]}"; do
    IFS="|" read -r uuid mountpoint fstype options passno description <<< "$entry"

    sudo_run mkdir -p "$mountpoint"
    sudo_run chown "$TARGET_UID:$TARGET_GID" "$mountpoint"
  done
}

unmount_by_uuid() {
  local uuid="$1"
  local description="$2"

  local dev
  dev="$(get_device_by_uuid "$uuid")"

  if [[ -z "$dev" ]]; then
    echo "UUID não encontrado para desmontagem: $uuid ($description)"
    return 0
  fi

  mapfile -t targets < <(findmnt -rn -S "$dev" -o TARGET 2>/dev/null || true)

  if [[ "${#targets[@]}" -eq 0 ]]; then
    echo "Nada montado atualmente para $description ($dev)."
    return 0
  fi

  echo
  echo "Montagens atuais encontradas para $description ($dev):"

  for target in "${targets[@]}"; do
    echo "  $target"
  done

  for target in "${targets[@]}"; do
    echo
    echo "Desmontando $description de:"
    echo "  $target"

    sudo_run umount "$target"
  done
}

unmount_configured_devices() {
  echo
  echo "Desmontando partições configuradas por UUID, independente do ambiente gráfico..."

  for entry in "${ENTRIES[@]}"; do
    IFS="|" read -r uuid mountpoint fstype options passno description <<< "$entry"
    unmount_by_uuid "$uuid" "$description"
  done
}

reload_and_test_mounts() {
  echo
  echo "Recarregando systemd..."
  sudo_run systemctl daemon-reload

  echo
  echo "Verificando sintaxe do fstab..."
  if [[ "$DRY_RUN" -eq 0 ]]; then
    sudo findmnt --verify
  else
    echo "+ sudo findmnt --verify"
  fi

  echo
  echo "Executando mount -a..."
  if [[ "$DRY_RUN" -eq 0 ]]; then
    sudo mount -a
  else
    echo "+ sudo mount -a"
  fi
}

enable_fstrim_timer() {
  echo
  echo "Ativando fstrim.timer para manutenção de SSD/NVMe..."

  sudo_run systemctl enable --now fstrim.timer
}

show_status() {
  print_header
  require_commands
  show_current_disks

  echo
  echo "Status das montagens configuradas:"
  echo "------------------------------------------------------------"

  for entry in "${ENTRIES[@]}"; do
    IFS="|" read -r uuid mountpoint fstype options passno description <<< "$entry"

    local dev
    dev="$(get_device_by_uuid "$uuid")"

    echo
    echo "$description"
    echo "  UUID: $uuid"
    echo "  Device: ${dev:-não encontrado}"
    echo "  Mountpoint esperado: $mountpoint"

    if [[ -n "$dev" ]]; then
      local current_targets
      current_targets="$(findmnt -rn -S "$dev" -o TARGET 2>/dev/null || true)"

      if [[ -n "$current_targets" ]]; then
        echo "  Montado atualmente em:"
        echo "$current_targets" | sed 's/^/    /'
      else
        echo "  Montado atualmente: não"
      fi
    fi

    if findmnt "$mountpoint" >/dev/null 2>&1; then
      echo "  Status do mountpoint fixo: montado"
      findmnt "$mountpoint" | sed 's/^/    /'
    else
      echo "  Status do mountpoint fixo: não montado"
    fi
  done

  echo
  echo "Bloco Victor no /etc/fstab:"
  echo "------------------------------------------------------------"
  if grep -qF "$MARK_BEGIN" "$FSTAB"; then
    sed -n "/$MARK_BEGIN/,/$MARK_END/p" "$FSTAB"
  else
    echo "Bloco não encontrado."
  fi
  echo "------------------------------------------------------------"

  echo
  echo "fstrim.timer:"
  systemctl is-enabled fstrim.timer 2>/dev/null || true
  systemctl is-active fstrim.timer 2>/dev/null || true

  pause
}

do_dry_run() {
  DRY_RUN=1

  print_header
  require_commands
  check_ntfs3_support
  show_current_disks
  verify_uuids
  preview_new_fstab

  echo
  echo "Simulação de ações:"
  create_mountpoints
  unmount_configured_devices
  backup_fstab
  apply_fstab_changes
  reload_and_test_mounts
  enable_fstrim_timer

  echo
  echo "Dry-run concluído. Nada foi alterado."
  echo "Log salvo em:"
  echo "  $LOG_FILE"

  pause
}

confirm_apply() {
  echo
  echo "ATENÇÃO:"
  echo "Esta ação vai alterar o /etc/fstab, criar pontos de montagem em /mnt,"
  echo "desmontar as partições pelos UUIDs atuais e remontar via fstab."
  echo
  echo "Digite APLICAR-FSTAB para continuar."
  read -rp "> " confirmation

  if [[ "$confirmation" != "APLICAR-FSTAB" ]]; then
    echo "Operação cancelada."
    pause
    return 1
  fi

  return 0
}

do_apply() {
  DRY_RUN=0

  print_header
  require_commands
  check_ntfs3_support
  show_current_disks
  verify_uuids

  confirm_apply || return 0

  create_mountpoints
  unmount_configured_devices
  backup_fstab
  apply_fstab_changes
  reload_and_test_mounts
  enable_fstrim_timer

  echo
  echo "============================================================"
  echo "Aplicação concluída."
  echo "============================================================"
  echo
  echo "Montagens configuradas:"
  echo "  WINDOWS        -> /mnt/windows"
  echo "  DADOS WINDOWS  -> /mnt/dados-windows"
  echo "  JOGOS LINUX    -> /mnt/jogos-linux"
  echo
  echo "Verifique com:"
  echo "  findmnt /mnt/windows"
  echo "  findmnt /mnt/dados-windows"
  echo "  findmnt /mnt/jogos-linux"
  echo
  echo "Log salvo em:"
  echo "  $LOG_FILE"

  pause
}

confirm_undo() {
  echo
  echo "ATENÇÃO:"
  echo "Esta ação vai remover apenas o bloco criado por este script no /etc/fstab."
  echo "Ela não apagará dados dos discos."
  echo
  echo "Digite REMOVER-FSTAB para continuar."
  read -rp "> " confirmation

  if [[ "$confirmation" != "REMOVER-FSTAB" ]]; then
    echo "Operação cancelada."
    pause
    return 1
  fi

  return 0
}

do_undo() {
  DRY_RUN=0

  print_header
  require_commands
  show_current_disks

  confirm_undo || return 0

  backup_fstab

  local tmp_new
  tmp_new="$(mktemp)"

  build_new_fstab_without_block "$FSTAB" "$tmp_new"

  echo
  echo "Removendo bloco Victor do /etc/fstab..."
  sudo_write_file "$FSTAB" "$tmp_new"
  rm -f "$tmp_new"

  echo
  echo "Recarregando systemd..."
  sudo_run systemctl daemon-reload

  echo
  echo "Verificando fstab..."
  sudo findmnt --verify || true

  echo
  echo "Undo concluído."
  echo
  echo "Os diretórios em /mnt foram mantidos:"
  echo "  /mnt/windows"
  echo "  /mnt/dados-windows"
  echo "  /mnt/jogos-linux"
  echo
  echo "Log salvo em:"
  echo "  $LOG_FILE"

  pause
}

main_menu() {
  while true; do
    print_header

    echo "Escolha uma opção:"
    echo
    echo "1) Apply"
    echo "2) Dry-run"
    echo "3) Status"
    echo "4) Undo"
    echo "5) Sair"
    echo
    read -rp "Opção: " option

    case "$option" in
      1)
        do_apply
        ;;
      2)
        do_dry_run
        ;;
      3)
        show_status
        ;;
      4)
        do_undo
        ;;
      5)
        echo "Saindo."
        exit 0
        ;;
      *)
        echo "Opção inválida."
        pause
        ;;
    esac
  done
}

main_menu
