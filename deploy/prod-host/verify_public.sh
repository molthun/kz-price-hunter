#!/usr/bin/env bash
# Проверка прода снаружи и на машине прода (P17-E). Только чтение. Код выхода 0 — только если прошли ВСЕ
# проверки; любая ошибка (включая поиск) даёт 1 (P17E-03).
#
#   ./verify_public.sh
#   CONTAINER=kzph-prod WATCHTOWER=kzph-prod-watchtower ./verify_public.sh   # шаблон deploy/prod-host
#
# По умолчанию — фактический прод: compose с NAS, перенесённый в ~/kzph-prod.
set -uo pipefail
URL="${URL:-https://shop.molthun.ru}"
CONTAINER="${CONTAINER:-kz-price-hunter}"
WATCHTOWER="${WATCHTOWER:-kz-price-watchtower}"
LOCAL_PORT="${LOCAL_PORT:-35540}"
EXPECTED_PEER="${EXPECTED_PEER:-192.168.28.10}"
fail=0
check() { printf '%-36s %s\n' "$1" "$2"; }
bad() { check "$1" "ОШИБКА: $2"; fail=1; }

# JSON-проверки — python3 хоста: читает тело из stdin, печатает итог или «err:…»
json_field() { python3 -c "import json, sys
try:
    d = json.load(sys.stdin)
    print($1)
except Exception as e:
    print('err:' + type(e).__name__)"; }

if ! docker ps --filter "name=^${CONTAINER}$" --format '{{.Status}}' | grep -q '^Up'; then
  bad "Контейнер $CONTAINER" "не запущен (имя задаётся CONTAINER=…)"
fi

local_v=$(curl -fsS -m 5 "http://127.0.0.1:${LOCAL_PORT}/api/version" 2>/dev/null | json_field 'd["version"]')
public_v=$(curl -fsS -m 10 "$URL/api/version" 2>/dev/null | json_field 'd["version"]')
if [[ -z "$local_v" || "$local_v" == err:* || -z "$public_v" || "$public_v" == err:* ]]; then
  bad "Версия здесь / снаружи" "${local_v:-нет ответа} / ${public_v:-нет ответа}"
elif [ "$local_v" != "$public_v" ]; then
  bad "Версия здесь / снаружи" "$local_v / $public_v — снаружи отвечает другой экземпляр"
else
  check "Версия здесь / снаружи" "$local_v"
fi

ok=0
for _ in $(seq 10); do
  status=$(curl -fsS -m 6 "$URL/api/health" 2>/dev/null | json_field 'd["status"]')
  [ "$status" = "ok" ] && ok=$((ok + 1))
  sleep 3
done
if [ "$ok" -ge 9 ]; then check "health снаружи (из 10)" "$ok"; else bad "health снаружи (из 10)" "$ok"; fi

# Поиск: HTTP 200 и JSON со списком products — время без ответа успехом не считается
body=$(mktemp)
meta=$(curl -sS -o "$body" -m 15 -w '%{http_code} %{time_total}' "$URL/api/products?search=iphone" 2>/dev/null) || meta="000 —"
code="${meta%% *}"
found=$(json_field 'len(d["products"]) if isinstance(d.get("products"), list) else "err:нет списка products"' < "$body")
rm -f "$body"
if [ "$code" != "200" ]; then
  bad "Поиск" "HTTP $code"
elif [[ "$found" == err:* || -z "$found" ]]; then
  bad "Поиск" "неверный ответ (${found:-пусто})"
else
  check "Поиск" "HTTP 200, ${meta#* } с, товаров на странице: $found"
fi

# Прокси DSM должен приходить с NAS: иначе TRUSTED_PROXIES неверен и все посетители попадают
# в один лимит по IP. Порт пробрасывается iptables, поэтому ss на хосте этих соединений не видит —
# смотрим таблицу TCP внутри контейнера (вместе с TIME_WAIT только что закрытых запросов).
peers=$(docker exec "$CONTAINER" python3 -c "
import socket, struct
seen = set()
for line in open('/proc/net/tcp').readlines()[1:]:
    p = line.split()
    if int(p[1].split(':')[1], 16) == 35539 and p[2] != '00000000:0000':
        ip = socket.inet_ntoa(struct.pack('<I', int(p[2].split(':')[0], 16)))
        if ip != '127.0.0.1':
            seen.add(ip)
print(' '.join(sorted(seen)))" 2>/dev/null)
case " $peers " in
  *" $EXPECTED_PEER "*) check "Соседи порта" "$peers" ;;
  *) bad "Соседи порта" "${peers:-нет соединений}, ждём $EXPECTED_PEER" ;;
esac

if docker ps --filter "name=^${WATCHTOWER}$" --format '{{.Status}}' | grep -q '^Up'; then
  if docker logs --since 24h "$WATCHTOWER" 2>&1 | grep -q "too old"; then
    bad "Watchtower" "ошибка API Docker (нужен DOCKER_API_VERSION=1.44)"
  else
    check "Watchtower" "работает"
  fi
else
  bad "Watchtower" "$WATCHTOWER не запущен (имя задаётся WATCHTOWER=…)"
fi

if [ "$fail" = 0 ]; then
  echo "ИТОГ: прод работает"
else
  echo "ИТОГ: есть отклонения — см. выше"
  exit 1
fi
