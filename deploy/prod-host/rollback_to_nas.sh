#!/usr/bin/env bash
# Откат прода на NAS (P17-E). Запускает ВЛАДЕЛЕЦ на машине стенда из ~/kzph-prod:
#
#   ./rollback_to_nas.sh               # NAS поднимается на своих прежних данных (данные с момента
#                                      # переезда остаются здесь, на NAS их нет)
#   ./rollback_to_nas.sh --with-data   # сначала переносит текущие данные отсюда на NAS;
#                                      # прежние данные NAS сохраняются как data.before-rollback-*
#
# Один раз спросит пароль sudo на NAS. После — вернуть в DSM прокси shop.molthun.ru на localhost:35539.
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
WITH_DATA=0
[ "${1:-}" = "--with-data" ] && WITH_DATA=1

say() { printf '\n==> %s\n' "$*"; }
die() { printf '\nОШИБКА: %s\n' "$*" >&2; exit 1; }

say "1. Остановка прода здесь (сначала Watchtower)"
docker stop kzph-prod-watchtower >/dev/null 2>&1 || true
"${COMPOSE[@]}" stop -t 60 app
curl -fsS -m 3 http://127.0.0.1:35540/api/live >/dev/null 2>&1 && die "прод здесь всё ещё отвечает"
# Не запускать снова после перезагрузки машины: второй экземпляр с тем же ботом
docker update --restart=no kzph-prod kzph-prod-watchtower >/dev/null

stamp=$(date +%Y%m%d-%H%M%S)
remote_restore=""
if [ "$WITH_DATA" = 1 ]; then
  say "2. Перенос текущих данных на NAS"
  tar -cf "$HERE/rollback-$stamp.tar" -C "$HERE" data
  nas_put "$HERE/rollback-$stamp.tar" "$NAS_DIR/rollback-$stamp.tar"
  rm -f "$HERE/rollback-$stamp.tar"
  remote_restore="cd $NAS_DIR && mv data data.before-rollback-$stamp && tar -xf rollback-$stamp.tar && rm -f rollback-$stamp.tar"
fi

say "3. Запуск прода на NAS (пароль sudo на NAS)"
nas_run "$NAS_SUDO sh -c '
  set -e
  ${remote_restore:-true}
  docker update --restart=unless-stopped $NAS_APP $NAS_WATCHTOWER >/dev/null
  docker start $NAS_APP >/dev/null
  docker start $NAS_WATCHTOWER >/dev/null
'"
for _ in $(seq 60); do
  curl -fsS -m 3 "$NAS_LIVE_URL/api/live" >/dev/null 2>&1 && break
  sleep 3
done
curl -fsS -m 3 "$NAS_LIVE_URL/api/version" || die "NAS не ответил за 3 минуты"

cat <<EOF

==> 4. Осталось вручную
   DSM → Обратный прокси → shop.molthun.ru: назначение обратно http://localhost:35539.
   Данные здесь (~/kzph-prod/data) не удалены.
EOF
