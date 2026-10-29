#!/usr/bin/env bash
# Ночная копия базы прода на другой диск или на NAS (P17-E). Запускается cron на машине прода:
#
#   30 4 * * * /home/molthun/kzph-prod/backup_to_nas.sh >> /home/molthun/kzph-prod/backup.log 2>&1
#
# Настройки — в backup.conf рядом со скриптом: CONTAINER=…, BACKUP_TARGET=… (локальный каталог на ДРУГОМ
# диске, например /srv/storage/kzph-backups, или host:каталог по SSH-ключу), KEEP_LOCAL, KEEP_REMOTE.
#
# Рядом с каждой копией базы кладётся набор конфигурации kzph-config-<та же метка>.tar (права 600):
# compose, .env и data/settings.json — без них из одной базы сервис не поднять (P17E-01, restore_prod.sh).
#
# Порядок (P17E-02): цель проверяется до копии (другой диск, место); копию делает само приложение
# (SQLite backup API + quick_check); после переноса проверяется КОНЕЧНЫЙ файл (размер и quick_check на
# цели), и только потом удаляются старые копии. Итог — «ok» или сбой с причиной — записывается в
# приложение (backup_deliveries), мониторинг показывает его в разделе «Резервные копии». Пропущенный
# запуск виден там же: успешной доставки нет дольше суток.
set -euo pipefail
# tar на macOS иначе кладёт в архив служебные ._* (проверки и тесты); на Linux ни на что не влияет
export COPYFILE_DISABLE=1

BACKUP_TARGET="${BACKUP_TARGET:-molthun@192.168.28.10:/volume1/Backup/kzph-prod}"
KEEP_LOCAL="${KEEP_LOCAL:-3}"
KEEP_REMOTE="${KEEP_REMOTE:-14}"
# Имя контейнера прода: kz-price-hunter — фактический прод (compose с NAS), kzph-prod — шаблон deploy/prod-host
CONTAINER="${CONTAINER:-kz-price-hunter}"
# Локальная цель обязана лежать на другом устройстве, чем данные: иначе копия гибнет вместе с диском базы
REQUIRE_OTHER_DEVICE="${REQUIRE_OTHER_DEVICE:-1}"
# Compose прода рядом со скриптом: compose.yaml — фактический прод, docker-compose.prod.yml — шаблон
COMPOSE_FILE="${COMPOSE_FILE:-compose.yaml}"
HERE="$(cd "$(dirname "$0")" && pwd)"
[ -f "$HERE/backup.conf" ] && . "$HERE/backup.conf"
DATA_HOST="${DATA_HOST:-$HERE/data}"

step="подготовка"
file=""
size=""

record() {  # record ok|failed "подробности" — итог доставки в приложение; сам по себе не роняет скрипт
  docker exec "$CONTAINER" python3 -c \
    "import sys, database; database.record_backup_delivery(*sys.argv[1:])" \
    "$1" "$BACKUP_TARGET" "$(basename "${file:-}")" "${size:-}" "$2" >/dev/null 2>&1 \
    || echo "$(date '+%F %T') не удалось записать итог доставки в приложение (контейнер $CONTAINER)"
}
fail() {
  trap - ERR
  echo "$(date '+%F %T') СБОЙ на шаге «${step}»: $1"
  record failed "$step: $1"
  exit 1
}
trap 'fail "команда завершилась с ошибкой (код $?)"' ERR

echo "$(date '+%F %T') старт → $BACKUP_TARGET"
remote=0
[[ "$BACKUP_TARGET" == *:* ]] && remote=1

step="проверка цели"
if [ "$remote" = 0 ]; then
  [ -d "$BACKUP_TARGET" ] || mkdir -p "$BACKUP_TARGET" 2>/dev/null || fail "каталог цели недоступен: $BACKUP_TARGET"
  [ -w "$BACKUP_TARGET" ] || fail "нет прав на запись в $BACKUP_TARGET"
  if [ "$REQUIRE_OTHER_DEVICE" = 1 ] && [ "$(stat -c %d "$BACKUP_TARGET")" = "$(stat -c %d "$DATA_HOST")" ]; then
    # Например, диск /srv/storage не смонтировался и каталог оказался на системном NVMe
    fail "цель на том же устройстве, что и база ($DATA_HOST) — диск не смонтирован?"
  fi
  db_bytes=$(stat -c %s "$DATA_HOST/prices.db")
  free_bytes=$(df --output=avail -B1 "$BACKUP_TARGET" | tail -1 | tr -d ' ')
  [ "$free_bytes" -gt $((db_bytes + db_bytes / 10)) ] || fail "мало места на цели: свободно $((free_bytes / 1048576)) МБ"
else
  remote_host="${BACKUP_TARGET%%:*}"
  remote_dir="${BACKUP_TARGET#*:}"
  ssh -o BatchMode=yes -o ConnectTimeout=15 "$remote_host" "mkdir -p '$remote_dir' && test -w '$remote_dir'" \
    || fail "цель по SSH недоступна или без прав на запись"
fi

step="копия базы"
path=$(docker exec "$CONTAINER" python3 -c "import database; print(database.backup_database('nightly') or '')") \
  || fail "приложение не сделало копию"
[ -n "$path" ] || fail "приложение не вернуло путь копии"
file="$DATA_HOST/backups/$(basename "$path")"
[ -f "$file" ] || fail "нет файла копии $file"
size=$(stat -c %s "$file")

step="перенос"
rsync -t --partial "$file" "$BACKUP_TARGET/" || fail "rsync вернул ошибку"

step="проверка конечного файла"
name="$(basename "$file")"
if [ "$remote" = 0 ]; then
  [ "$(stat -c %s "$BACKUP_TARGET/$name" 2>/dev/null)" = "$size" ] || fail "размер на цели не совпадает с копией"
  image=$(docker inspect -f '{{.Config.Image}}' "$CONTAINER")
  verdict=$(docker run --rm --network none -v "$BACKUP_TARGET:/b:ro" --entrypoint python3 "$image" -c \
    "import sqlite3, sys; c = sqlite3.connect('file:/b/' + sys.argv[1] + '?immutable=1', uri=True); print(c.execute('PRAGMA quick_check').fetchone()[0])" \
    "$name" 2>&1 | tail -1) || true
  [ "$verdict" = "ok" ] || fail "копия на цели не прошла quick_check: ${verdict:-нет ответа}"
else
  # На удалённой стороне (NAS: python3 и sqlite есть) — тот же quick_check конечного файла (P17ER-02).
  # Не удалось выполнить проверку — это сбой: успех без проверки не записывается и старые копии не трогаются
  remote_size=$(ssh -o BatchMode=yes "$remote_host" "stat -c %s '$remote_dir/$name'") || fail "не прочитан размер на цели"
  [ "$remote_size" = "$size" ] || fail "размер на цели не совпадает с копией"
  verdict=$(ssh -o BatchMode=yes "$remote_host" \
    "python3 -c 'import sqlite3, sys; c = sqlite3.connect(\"file:\" + sys.argv[1] + \"?immutable=1\", uri=True); print(c.execute(\"PRAGMA quick_check\").fetchone()[0])' '$remote_dir/$name'" \
    2>&1 | tail -1) || true
  [ "$verdict" = "ok" ] || fail "копия на цели не прошла quick_check: ${verdict:-проверка на цели не выполнилась}"
fi

step="конфигурация"
# Набор для восстановления сервиса целиком: compose, .env (токены) и settings.json (настройки, ключи AI).
# settings.json приложение пишет от root с правами 600 — читается через контейнер.
config="kzph-config-${name#prices-nightly-}"
config="${config%.db}.tar"
[ -f "$HERE/$COMPOSE_FILE" ] || fail "нет $COMPOSE_FILE рядом со скриптом"
[ -f "$HERE/.env" ] || fail "нет .env рядом со скриптом"
bundle=$(mktemp -d)
chmod 700 "$bundle"
cp "$HERE/$COMPOSE_FILE" "$bundle/compose.yaml"
cp "$HERE/.env" "$bundle/.env"
docker exec "$CONTAINER" cat /app/data/settings.json > "$bundle/settings.json" || fail "settings.json не прочитан"
python3 -c "import json, sys; json.load(open(sys.argv[1]))" "$bundle/settings.json" || fail "settings.json не JSON"
(umask 077 && tar -C "$bundle" -cf "$bundle/$config" compose.yaml .env settings.json)
rm -f "$bundle/compose.yaml" "$bundle/.env" "$bundle/settings.json"
if [ "$remote" = 0 ]; then
  install -m 600 "$bundle/$config" "$BACKUP_TARGET/$config"
  [ "$(tar -tf "$BACKUP_TARGET/$config" | LC_ALL=C sort | tr '\n' ' ')" = ".env compose.yaml settings.json " ] \
    || fail "набор конфигурации на цели неполный"
else
  rsync -t --chmod=F600 "$bundle/$config" "$BACKUP_TARGET/" || fail "rsync конфигурации вернул ошибку"
  members=$(ssh -o BatchMode=yes "$remote_host" "tar -tf '$remote_dir/$config'" | LC_ALL=C sort | tr '\n' ' ') \
    || fail "набор конфигурации на цели не прочитан"
  [ "$members" = ".env compose.yaml settings.json " ] || fail "набор конфигурации на цели неполный"
fi
rm -rf "$bundle"

step="удаление старых копий"
# Только ночные копии и только после проверки новой; копии перед миграциями (prices-pre-v*) не трогаются
if [ "$remote" = 0 ]; then
  (cd "$BACKUP_TARGET" && for mask in 'prices-nightly-*.db' 'kzph-config-*.tar'; do
     ls -1t $mask 2>/dev/null | tail -n +$((KEEP_REMOTE + 1)) | xargs -r rm -f; done)
else
  ssh -o BatchMode=yes "$remote_host" \
    "cd '$remote_dir' && for m in 'prices-nightly-*.db' 'kzph-config-*.tar'; do ls -1t \$m 2>/dev/null | tail -n +$((KEEP_REMOTE + 1)) | xargs -r rm -f; done"
fi
# Каталог backups создаёт приложение от root внутри контейнера: чистится тоже изнутри
docker exec "$CONTAINER" sh -c \
  "ls -1t /app/data/backups/prices-nightly-*.db 2>/dev/null | tail -n +$((KEEP_LOCAL + 1)) | xargs -r rm -f"

trap - ERR
record ok "база: размер и quick_check на цели сверены; конфигурация ${config}: 3 файла на цели"
echo "$(date '+%F %T') готово: $name ($((size / 1048576)) МБ) + $config → $BACKUP_TARGET, проверено на цели"
