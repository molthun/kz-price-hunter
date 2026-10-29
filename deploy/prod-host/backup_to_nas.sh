#!/usr/bin/env bash
# Ночная копия базы прода на другой диск или на NAS (P17-E). Запускается cron на машине стенда:
#
# Цель — host:каталог (SSH-ключ к NAS) или локальный каталог на другом диске, например
# /srv/storage/kzph-backups. Задаётся в backup.conf рядом со скриптом: BACKUP_TARGET=…, CONTAINER=…
#
#   30 4 * * * /home/molthun/kzph-prod/backup_to_nas.sh >> /home/molthun/kzph-prod/backup.log 2>&1
#
# Копия делается самим приложением (SQLite backup API + quick_check, пульс «резервные копии»
# в мониторинге), затем уходит на NAS по SSH-ключу. Здесь хранится KEEP_LOCAL последних,
# на NAS — KEEP_REMOTE. До переезда регулярных копий не было: последняя на NAS — 19.09.2026.
set -euo pipefail

BACKUP_TARGET="${BACKUP_TARGET:-molthun@192.168.28.10:/volume1/Backup/kzph-prod}"
KEEP_LOCAL="${KEEP_LOCAL:-3}"
KEEP_REMOTE="${KEEP_REMOTE:-14}"
# Имя контейнера прода: kzph-prod по deploy/prod-host, kz-price-hunter — если перенесён compose с NAS
CONTAINER="${CONTAINER:-kzph-prod}"
HERE="$(cd "$(dirname "$0")" && pwd)"
[ -f "$HERE/backup.conf" ] && . "$HERE/backup.conf"

echo "$(date '+%F %T') старт"
path=$(docker exec "$CONTAINER" python3 -c "import database; print(database.backup_database('nightly') or '')")
[ -n "$path" ] || { echo "копия не создана"; exit 1; }
file="$HERE/data/backups/$(basename "$path")"
[ -f "$file" ] || { echo "нет файла $file"; exit 1; }

if [[ "$BACKUP_TARGET" == *:* ]]; then
  remote_host="${BACKUP_TARGET%%:*}"
  remote_dir="${BACKUP_TARGET#*:}"
  ssh -o BatchMode=yes "$remote_host" "mkdir -p '$remote_dir'"
  rsync -t --partial "$file" "$BACKUP_TARGET/"
  # Удаляются только ночные копии; копии перед миграциями (prices-pre-v*) не трогаются
  ssh -o BatchMode=yes "$remote_host" \
    "cd '$remote_dir' && ls -1t prices-nightly-*.db 2>/dev/null | tail -n +$((KEEP_REMOTE + 1)) | xargs -r rm -f"
else
  # Локальный каталог (проверка или папка, которую Synology Drive сам уносит на NAS)
  mkdir -p "$BACKUP_TARGET"
  rsync -t --partial "$file" "$BACKUP_TARGET/"
  (cd "$BACKUP_TARGET" && ls -1t prices-nightly-*.db 2>/dev/null | tail -n +$((KEEP_REMOTE + 1)) | xargs -r rm -f)
fi
# Каталог backups создаёт приложение от root внутри контейнера: чистится тоже изнутри
docker exec "$CONTAINER" sh -c \
  "ls -1t /app/data/backups/prices-nightly-*.db 2>/dev/null | tail -n +$((KEEP_LOCAL + 1)) | xargs -r rm -f"
echo "$(date '+%F %T') готово: $(basename "$file") ($(($(stat -c %s "$file") / 1048576)) МБ) → $BACKUP_TARGET"
