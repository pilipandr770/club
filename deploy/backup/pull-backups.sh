#!/bin/sh
# Копия дампов с сервера на ваш компьютер (запускать у себя, не на сервере): бэкап на том же VPS не защищает от потери VPS.
# Пример: ./deploy/backup/pull-backups.sh ~/club-backups
# Нужен ssh-ключ к серверу (по умолчанию ~/.ssh/hostinger_vps). Адрес и каталог можно задать переменными CLUB_HOST и CLUB_DIR.
set -eu

target="${1:?Zielordner angeben, z. B. ~/club-backups}"
host="${CLUB_HOST:-root@187.124.6.120}"
remote="${CLUB_DIR:-/opt/club}/backups/"
key="${CLUB_SSH_KEY:-$HOME/.ssh/hostinger_vps}"

mkdir -p "$target"
if command -v rsync >/dev/null 2>&1; then
  rsync -av --ignore-existing -e "ssh -i $key" "$host:$remote" "$target/"
else
  scp -i "$key" -p "$host:${remote}club-*.dump" "$target/"
fi
echo "Kopien in $target:"
ls -1 "$target" | tail -n 5
