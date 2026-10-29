#!/usr/bin/env bash
# Восстановление прода из ночного набора (P17E-01): копия базы prices-nightly-*.db + конфигурация
# kzph-config-*.tar (compose.yaml, .env, settings.json) из каталога копий. Порядок и сценарии —
# docs/PROD_MOVE_P17E.md, раздел «Восстановление».
#
#   ./restore_prod.sh --to ~/kzph-restore --rehearsal --start   # репетиция: изолированно, без бота и внешних сетей
#   ./restore_prod.sh --to ~/kzph-prod-new                      # подготовить настоящий прод, не запуская
#   ./restore_prod.sh --to ~/kzph-prod-new --start              # запустить, только если старый прод не отвечает
#
# Параметры: --from КАТАЛОГ (по умолчанию /srv/storage/kzph-backups), --db ФАЙЛ и --config ФАЙЛ (по
# умолчанию самая свежая копия и её парный набор), --old-url (старый прод, который обязан молчать перед
# --start; по умолчанию http://127.0.0.1:35540), --image (образ для проверки базы).
# Значения .env и settings.json скрипт не печатает.
set -euo pipefail
# tar на macOS иначе кладёт в архив служебные ._* (проверки и тесты); на Linux ни на что не влияет
export COPYFILE_DISABLE=1

FROM="/srv/storage/kzph-backups"
TO=""
DB=""
CONFIG=""
REHEARSAL=0
START=0
OLD_URL="${OLD_URL:-http://127.0.0.1:35540}"
IMAGE="${IMAGE:-ghcr.io/molthun/kz-price-hunter:latest}"
REHEARSAL_PROJECT="kzph-restore-rehearsal"

while [ $# -gt 0 ]; do
  case "$1" in
    --from) FROM="$2"; shift 2 ;;
    --to) TO="$2"; shift 2 ;;
    --db) DB="$2"; shift 2 ;;
    --config) CONFIG="$2"; shift 2 ;;
    --old-url) OLD_URL="$2"; shift 2 ;;
    --image) IMAGE="$2"; shift 2 ;;
    --rehearsal) REHEARSAL=1; shift ;;
    --start) START=1; shift ;;
    *) echo "неизвестный параметр: $1" >&2; exit 2 ;;
  esac
done

say() { printf '\n==> %s\n' "$*"; }
die() { printf '\nОШИБКА: %s\n' "$*" >&2; exit 1; }

[ -n "$TO" ] || die "укажите --to КАТАЛОГ"
[ -e "$TO/data/prices.db" ] && die "в $TO уже есть data/prices.db — восстановление не перезаписывает данные"

say "1. Набор для восстановления"
if [ -z "$DB" ]; then
  DB=$(ls -1t "$FROM"/prices-nightly-*.db 2>/dev/null | head -1 || true)
fi
[ -n "$DB" ] && [ -f "$DB" ] || die "нет копии базы (prices-nightly-*.db) в $FROM"
if [ -z "$CONFIG" ]; then
  # Пара по метке копии; если её нет — самый свежий набор конфигурации
  pair="$FROM/kzph-config-$(basename "${DB%.db}" | sed 's/^prices-nightly-//').tar"
  if [ -f "$pair" ]; then CONFIG="$pair"; else CONFIG=$(ls -1t "$FROM"/kzph-config-*.tar 2>/dev/null | head -1 || true); fi
fi
[ -n "$CONFIG" ] && [ -f "$CONFIG" ] || die "нет набора конфигурации (kzph-config-*.tar) в $FROM"
members=$(tar -tf "$CONFIG" | LC_ALL=C sort | tr '\n' ' ')
[ "$members" = ".env compose.yaml settings.json " ] || die "набор конфигурации неполный: $members"
echo "   база:         $DB ($(($(stat -c %s "$DB") / 1048576)) МБ)"
echo "   конфигурация: $CONFIG"

say "2. Проверка базы до развёртывания"
db_dir=$(cd "$(dirname "$DB")" && pwd)
facts=$(docker run --rm --network none -v "$db_dir:/b:ro" --entrypoint python3 "$IMAGE" -c "
import sqlite3, sys
c = sqlite3.connect('file:/b/' + sys.argv[1] + '?immutable=1', uri=True)
ok = c.execute('PRAGMA quick_check').fetchone()[0]
n = lambda t: c.execute('SELECT count(*) FROM ' + t).fetchone()[0]
print(ok, n('products'), n('price_observations'), n('watches'))" "$(basename "$DB")" 2>&1 | tail -1)
read -r qc n_products n_observations n_watches <<<"$facts"
[ "$qc" = "ok" ] || die "копия базы не прошла quick_check: $facts"
echo "   quick_check ok: товаров $n_products, наблюдений цен $n_observations, наблюдений пользователей $n_watches"

say "3. Развёртывание в $TO"
mkdir -p "$TO/data"
chmod 700 "$TO"
tar -xf "$CONFIG" -C "$TO" compose.yaml .env settings.json
mv "$TO/settings.json" "$TO/data/settings.json"
chmod 600 "$TO/.env" "$TO/data/settings.json"
cp "$DB" "$TO/data/prices.db"

compose=(docker compose -f "$TO/compose.yaml")
container=""
if [ "$REHEARSAL" = 1 ]; then
  # Репетиция не должна ничего отправлять наружу: без токена бота и AI-ключей, без суточной сводки, во
  # внутренней сети Docker без выхода в интернет и без опубликованных портов. Выключателя автообхода
  # в приложении нет — обходы запускаются, но наружу не выходят (сеть internal) и пишут только в эту копию. Исходный compose
  # остаётся рядом (compose.yaml.orig) — проверяется, что он есть в наборе, но запускается не он.
  mv "$TO/compose.yaml" "$TO/compose.yaml.orig"
  python3 - "$TO/.env" "$TO/data/settings.json" <<'EOF'
import json, sys
env_path, settings_path = sys.argv[1], sys.argv[2]
lines = [l for l in open(env_path).read().splitlines() if not l.startswith(("TELEGRAM_BOT_TOKEN=", "DIAG_TOKEN="))]
open(env_path, "w").write("\n".join(lines + ["TELEGRAM_BOT_TOKEN=", "DIAG_TOKEN="]) + "\n")
s = json.load(open(settings_path))
s.update({"telegram_bot_token": "", "telegram_chat_id": "", "gemini_api_key": "", "openai_api_key": "",
          "ai_search_enabled": False, "daily_digest_telegram_enabled": False})
json.dump(s, open(settings_path, "w"), ensure_ascii=False, indent=2)
EOF
  cat > "$TO/compose.yaml" <<EOF
# Репетиция восстановления (restore_prod.sh --rehearsal): изолированная сеть, без портов и без бота
name: $REHEARSAL_PROJECT
services:
  app:
    image: $IMAGE
    container_name: $REHEARSAL_PROJECT
    env_file: .env
    environment:
      - DATA_DIR=/app/data
      - TZ=Asia/Almaty
      - PORT=35539
      - ALLOW_DEV_LOGIN=0
      - TELEGRAM_BOT_TOKEN=
      - GEMINI_API_KEY=
      - OPENAI_API_KEY=
    volumes:
      - ./data:/app/data
    networks: [isolated]
networks:
  isolated:
    internal: true
EOF
  container="$REHEARSAL_PROJECT"
  echo "   репетиция: бот, AI и сводка выключены; сеть internal без выхода наружу, портов нет"
fi

if [ "$START" = 0 ]; then
  say "Готово без запуска. Запуск: ${compose[*]} up -d (настоящий — только после остановки старого прода)"
  exit 0
fi

say "4. Запуск"
if [ "$REHEARSAL" = 0 ]; then
  # Два экземпляра с одним ботом ломают Telegram (HTTP 409) и пишут каждый в свою базу
  if curl -fsS -m 5 "$OLD_URL/api/live" >/dev/null 2>&1; then
    die "старый прод ($OLD_URL) ещё отвечает — сначала остановите его и запретите автозапуск (restart=no)"
  fi
fi
"${compose[@]}" up -d
[ -n "$container" ] || container=$("${compose[@]}" ps --format '{{.Name}}' | grep -v -i watchtower | head -1)

inside() {  # запрос к приложению изнутри контейнера: у репетиции нет опубликованных портов
  docker exec "$container" python3 -c "
import json, sys, urllib.request
try:
    print(json.dumps(json.load(urllib.request.urlopen('http://127.0.0.1:35539' + sys.argv[1], timeout=10))))
except Exception as e:
    print(json.dumps({'error': type(e).__name__}))" "$1"
}
for _ in $(seq 60); do
  inside /api/health | grep -q '"status": "ok"' && break
  sleep 3
done
health=$(inside /api/health)
echo "$health" | grep -q '"status": "ok"' || die "health не ok за 3 минуты: $health"

say "5. Проверка восстановленного сервиса"
search=$(inside "/api/products?search=iphone")
found=$(echo "$search" | python3 -c "import json, sys; d = json.load(sys.stdin); print(len(d['products']) if isinstance(d.get('products'), list) else 'err')")
[ "$found" != "err" ] && [ "$found" -gt 0 ] || die "поиск не вернул товаров: $search"
counts=$(docker exec "$container" python3 -c "
import config, database
s = config.load_settings()
with database.get_connection() as c:
    n = lambda t: c.execute('SELECT count(*) FROM ' + t).fetchone()[0]
    print(n('products'), n('price_observations'), n('watches'), len(s.get('enabled_shops') or []))")
read -r r_products r_observations r_watches r_shops <<<"$counts"
[ "$r_products" -ge "$n_products" ] && [ "$r_observations" -ge "$n_observations" ] && [ "$r_watches" = "$n_watches" ] \
  || die "данные не совпали с копией: было $n_products/$n_observations/$n_watches, стало $counts"
[ "$r_shops" -gt 0 ] || die "настройки не восстановлены: список магазинов пуст"
echo "   версия $(inside /api/version | python3 -c "import json, sys; print(json.load(sys.stdin).get('version'))")," \
     "health ok, поиск: $found товаров на странице"
echo "   товаров $r_products, наблюдений цен $r_observations, наблюдений пользователей $r_watches," \
     "магазинов в настройках $r_shops"
if [ "$REHEARSAL" = 1 ]; then
  say "Репетиция пройдена. Убрать: ${compose[*]} down && rm -rf $TO"
else
  say "Прод запущен. Дальше: переключить прокси DSM на этот порт и запустить verify_public.sh"
fi
