#!/usr/bin/env bash
# Переезд прода с NAS на машину стенда (P17-E). Запускает ВЛАДЕЛЕЦ на машине стенда из ~/kzph-prod:
#
#   ./move_from_nas.sh            # проверки, остановка NAS, перенос данных, запуск здесь
#   ./move_from_nas.sh --check    # только предварительные проверки, ничего не меняет
#
# Один раз спросит пароль sudo на NAS (остановка контейнеров и упаковка данных — от root).
# Значения из .env скрипт не печатает. Подробности и откат — docs/PROD_MOVE_P17E.md.
set -euo pipefail

NAS_SSH="${NAS_SSH:-molthun@192.168.28.10}"
NAS_DIR="${NAS_DIR:-/volume1/docker/kz-price-hunter}"
NAS_APP="${NAS_APP:-kz-price-hunter}"
NAS_WATCHTOWER="${NAS_WATCHTOWER:-kz-price-watchtower}"
# Репетиция: NAS_SSH=local выполняет «команды NAS» здесь же, без ssh, а NAS_SUDO= — без sudo.
# Так весь цикл проверяется на машине стенда с поддельным NAS-контейнером (docs/PROD_MOVE_P17E.md).
NAS_SUDO="${NAS_SUDO-sudo}"
NAS_LIVE_URL="${NAS_LIVE_URL:-http://192.168.28.10:35539}"
nas_run() { if [ "$NAS_SSH" = local ]; then sh -c "$1"; else ssh -t "$NAS_SSH" "$1"; fi; }
nas_out() { if [ "$NAS_SSH" = local ]; then sh -c "$1"; else ssh "$NAS_SSH" "$1"; fi; }
nas_get() { if [ "$NAS_SSH" = local ]; then cp "$1" "$2"; else scp -q "$NAS_SSH:$1" "$2"; fi; }
nas_put() { if [ "$NAS_SSH" = local ]; then cp "$1" "$2"; else scp -q "$1" "$NAS_SSH:$2"; fi; }
HERE="$(cd "$(dirname "$0")" && pwd)"
COMPOSE=(docker compose -f "$HERE/docker-compose.prod.yml")
LOCAL_URL="http://127.0.0.1:35540"
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

say() { printf '\n==> %s\n' "$*"; }
die() { printf '\nОШИБКА: %s\n' "$*" >&2; exit 1; }

say "1. Предварительные проверки"
[ -f "$HERE/docker-compose.prod.yml" ] || die "нет docker-compose.prod.yml рядом со скриптом"
[ -f "$HERE/.env" ] || die "нет .env (cp prod.env.example .env && chmod 600 .env и заполнить)"
[ "$(stat -c %a "$HERE/.env")" = "600" ] || die ".env должен иметь права 600: chmod 600 .env"
for key in APP_URL PUBLIC_ORIGIN TRUSTED_PROXIES TELEGRAM_BOT_TOKEN ADMIN_TELEGRAM_IDS; do
  grep -Eq "^${key}=.+" "$HERE/.env" || die "в .env пусто: $key"
done
if [ -e "$HERE/data/prices.db" ]; then
  die "в $HERE/data уже есть prices.db — переезд не перезаписывает данные. Уберите каталог сознательно."
fi
if [ "$NAS_SSH" != local ]; then
  ssh -o BatchMode=yes -o ConnectTimeout=8 "$NAS_SSH" true 2>/dev/null \
    || ssh -o ConnectTimeout=8 "$NAS_SSH" true || die "нет SSH до $NAS_SSH"
fi
db_bytes=$(nas_out "stat -c %s '$NAS_DIR/data/prices.db'") || die "не прочитан размер базы на NAS"
free_bytes=$(df --output=avail -B1 "$HERE" | tail -1)
[ "$free_bytes" -gt $((db_bytes * 3)) ] || die "мало места: нужно ≥ 3× базы ($((db_bytes / 1048576)) МБ)"
"${COMPOSE[@]}" pull --quiet || die "образ ghcr.io/molthun/kz-price-hunter:latest не скачался"
curl -fsS -m 5 "$NAS_LIVE_URL/api/live" >/dev/null && echo "   NAS-прод сейчас отвечает" \
  || echo "   NAS-прод сейчас не отвечает (это не ошибка проверки)"
echo "   База на NAS: $((db_bytes / 1048576)) МБ, свободно здесь: $((free_bytes / 1073741824)) ГБ"
[ "$CHECK_ONLY" = 1 ] && { say "Проверки пройдены, ничего не изменено (--check)"; exit 0; }

say "2. Остановка прода на NAS и упаковка данных (пароль sudo на NAS)"
# Сначала Watchtower, чтобы он не поднял контейнер обратно; restart=no — чтобы NAS не запустил
# его после перезагрузки: два экземпляра с одним ботом ломают Telegram (HTTP 409) и пишут
# каждый в свою базу.
stamp=$(date +%Y%m%d-%H%M%S)
nas_run "$NAS_SUDO sh -c '
  set -e
  docker update --restart=no $NAS_WATCHTOWER $NAS_APP >/dev/null
  docker stop $NAS_WATCHTOWER >/dev/null
  docker stop -t 60 $NAS_APP >/dev/null
  cd $NAS_DIR
  tar -cf move-$stamp.tar data
  chmod 644 move-$stamp.tar
'"
if curl -fsS -m 5 "$NAS_LIVE_URL/api/live" >/dev/null 2>&1; then
  die "прод на NAS всё ещё отвечает — остановка не удалась, переезд прерван"
fi
echo "   NAS остановлен, архив данных: $NAS_DIR/move-$stamp.tar"

say "3. Перенос данных на эту машину"
nas_get "$NAS_DIR/move-$stamp.tar" "$HERE/move-$stamp.tar"
tar -xf "$HERE/move-$stamp.tar" -C "$HERE"
[ -f "$HERE/data/prices.db" ] || die "в архиве нет data/prices.db"
echo "   Проверка целостности копии (quick_check)…"
check=$(docker run --rm -v "$HERE/data:/d" --entrypoint python3 ghcr.io/molthun/kz-price-hunter:latest \
  -c "import sqlite3; c=sqlite3.connect('/d/prices.db'); print(c.execute('PRAGMA quick_check').fetchone()[0], c.execute('SELECT count(*) FROM products').fetchone()[0])")
case "$check" in ok\ *) echo "   quick_check ok, товаров: ${check#ok }";; *) die "копия базы повреждена: $check";; esac

say "4. Запуск прода здесь"
"${COMPOSE[@]}" up -d
for _ in $(seq 60); do
  curl -fsS -m 3 "$LOCAL_URL/api/health" 2>/dev/null | grep -q '"status": "ok"' && break
  sleep 3
done
curl -fsS -m 3 "$LOCAL_URL/api/health" | grep -q '"status": "ok"' || die "health не ok за 3 минуты: docker logs kzph-prod"
echo "   $(curl -fsS "$LOCAL_URL/api/version")"
rm -f "$HERE/move-$stamp.tar"

cat <<EOF

==> 5. Осталось вручную
   DSM → Панель управления → Портал входа → Дополнительно → Обратный прокси → shop.molthun.ru:
     назначение http://192.168.28.25:35540 (было localhost:35539).
   Затем: ./verify_public.sh
   Данные NAS остались на месте ($NAS_DIR/data) — это холодный резерв; архив move-$stamp.tar
   на NAS можно удалить после успешной проверки.
   Откат: ./rollback_to_nas.sh (см. docs/PROD_MOVE_P17E.md).
EOF
