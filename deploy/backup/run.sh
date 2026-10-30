#!/bin/sh
# Цикл сервиса backup: дамп каждую ночь в 03:00 UTC, по воскресеньям после него проверка восстановления.
# Если свежего дампа (меньше 24 часов) нет, первый дамп делается сразу при старте.
DIR="${BACKUP_DIR:-/backups}"
mkdir -p "$DIR"
chmod 700 "$DIR"

next_run() {
  now="$(date -u +%s)"
  target="$(date -u -d 'today 03:00' +%s)"
  [ "$target" -gt "$now" ] || target="$(date -u -d 'tomorrow 03:00' +%s)"
  echo $((target - now))
}

if [ -z "$(find "$DIR" -name 'club-*.dump' -mmin -1440 2>/dev/null | head -n 1)" ]; then
  sh /backup/backup.sh || echo "backup: FEHLGESCHLAGEN" >&2
fi

while true; do
  sleep "$(next_run)"
  sh /backup/backup.sh || echo "backup: FEHLGESCHLAGEN" >&2
  if [ "$(date -u +%u)" = "7" ]; then
    sh /backup/verify.sh || echo "verify: FEHLGESCHLAGEN" >&2
  fi
done
