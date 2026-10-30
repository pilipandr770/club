#!/bin/sh
# Проверка восстановления: свежий дамп разворачивается во временную базу, там сверяются таблицы и число участников.
# Дамп, который не удаётся восстановить, не бэкап. Живая база не затрагивается.
set -eu

DIR="${BACKUP_DIR:-/backups}"
export PGPASSWORD="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD fehlt}"
SCRATCH=club_restore_check

latest="$(ls -1 "$DIR"/club-*.dump 2>/dev/null | tail -n 1 || true)"
[ -n "$latest" ] || { echo "verify: kein Dump gefunden" >&2; exit 1; }

psql -h db -U club -d postgres -v ON_ERROR_STOP=1 -q \
  -c "DROP DATABASE IF EXISTS $SCRATCH" -c "CREATE DATABASE $SCRATCH"
trap 'psql -h db -U club -d postgres -q -c "DROP DATABASE IF EXISTS '"$SCRATCH"'" >/dev/null 2>&1 || true' EXIT

pg_restore -h db -U club -d "$SCRATCH" --no-owner --exit-on-error "$latest"

restored="$(psql -h db -U club -d "$SCRATCH" -tA -c 'SELECT count(*) FROM users')"
live="$(psql -h db -U club -d club -tA -c 'SELECT count(*) FROM users')"
tables="$(psql -h db -U club -d "$SCRATCH" -tA -c "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")"

# Живых участников могло стать больше, но не меньше (кроме удалений за сутки), и пустой дамп при непустой базе недопустим
if [ "$live" -gt 0 ] && [ "$restored" -eq 0 ]; then
  echo "verify: Dump $latest enthält keine Mitglieder, live: $live" >&2
  exit 1
fi
[ "$tables" -gt 10 ] || { echo "verify: nur $tables Tabellen wiederhergestellt" >&2; exit 1; }

touch "$DIR/.last_verify_ok"
echo "verify: $latest ok (Mitglieder $restored von $live live, Tabellen $tables)"
