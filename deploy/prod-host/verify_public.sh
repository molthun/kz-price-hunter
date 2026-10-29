#!/usr/bin/env bash
# Проверка прода после переключения прокси DSM (P17-E). Только чтение. Запускать на машине стенда.
set -uo pipefail
URL="${URL:-https://shop.molthun.ru}"
fail=0
check() { printf '%-34s %s\n' "$1" "$2"; }

local_v=$(curl -fsS -m 5 http://127.0.0.1:35540/api/version 2>/dev/null)
public_v=$(curl -fsS -m 10 "$URL/api/version" 2>/dev/null)
check "Версия здесь / снаружи" "${local_v:-нет ответа} / ${public_v:-нет ответа}"
[ -n "$public_v" ] && [ "$local_v" = "$public_v" ] || fail=1

ok=0
for _ in $(seq 10); do
  curl -fsS -m 6 "$URL/api/health" 2>/dev/null | grep -q '"status": "ok"' && ok=$((ok + 1))
  sleep 3
done
check "health снаружи (из 10)" "$ok"
[ "$ok" -ge 9 ] || fail=1

t=$(curl -fsS -o /dev/null -m 15 -w '%{time_total}' "$URL/api/products?search=iphone" 2>/dev/null || echo "—")
check "Поиск, с" "$t"

# Прокси DSM должен приходить с NAS: иначе TRUSTED_PROXIES неверен и все посетители попадают
# в один лимит по IP. Порт пробрасывается iptables, поэтому ss на хосте этих соединений не видит —
# смотрим таблицу TCP внутри контейнера (вместе с TIME_WAIT только что закрытых запросов).
peers=$(docker exec kzph-prod python3 -c "
import socket, struct
seen = set()
for line in open('/proc/net/tcp').readlines()[1:]:
    p = line.split()
    if int(p[1].split(':')[1], 16) == 35539 and p[2] != '00000000:0000':
        ip = socket.inet_ntoa(struct.pack('<I', int(p[2].split(':')[0], 16)))
        if ip != '127.0.0.1':
            seen.add(ip)
print(' '.join(sorted(seen)))" 2>/dev/null)
check "Соседи порта (ждём 192.168.28.10)" "${peers:-нет соединений}"
case " $peers " in *" 192.168.28.10 "*) ;; *) fail=1;; esac

if docker ps --filter name=kzph-prod-watchtower --format '{{.Status}}' | grep -q Up; then
  check "Watchtower" "работает"
  docker logs --since 10m kzph-prod-watchtower 2>&1 | grep -q "too old" && { check "" "ОШИБКА API Docker"; fail=1; }
else
  check "Watchtower" "НЕ ЗАПУЩЕН"; fail=1
fi
[ "$fail" = 0 ] && echo "ИТОГ: прод на новой машине работает" || { echo "ИТОГ: есть отклонения — см. выше"; exit 1; }
