#!/bin/sh
# Ночной дамп базы клуба. Таблицы law_* (корпус законов) сохраняем только по схеме: данные пересоздаёт flask laws-import.
# Дамп пишется во временный файл и переименовывается после успеха, поэтому оборванный дамп никогда не выглядит готовым.
set -eu

DIR="${BACKUP_DIR:-/backups}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
export PGPASSWORD="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD fehlt}"

umask 077
stamp="$(date -u +%Y%m%d-%H%M%S)"
file="$DIR/club-$stamp.dump"

pg_dump -h db -U club -d club -Fc --exclude-table-data='law_*' -f "$file.part"
mv "$file.part" "$file"
find "$DIR" -name 'club-*.dump' -mtime +"$KEEP_DAYS" -delete
find "$DIR" -name 'club-*.dump.part' -mmin +120 -delete
touch "$DIR/.last_ok"
echo "backup: $file ($(du -h "$file" | cut -f1))"
