#!/bin/sh
# Сбор только чтением после 14:25Z: логи планировщика и срезы БД (q14/q15/q17/q18)
until [ "$(date -u +%H%M)" -ge 1425 ]; do sleep 60; done
O=/tmp/kzph-6428-live; mkdir -p $O
docker logs -t --since 2026-11-21T09:14:00Z kzph-stand 2>&1 | grep -E "AutoScan\]|Wave\] 🌊|Wave\] 🏁|Старт обхода|\] (complete|partial|failed|limited):" > $O/scheduler.log
for q in kzph_ro_q14 kzph_ro_q15 kzph_ro_q17 kzph_ro_q18; do echo "## $q"; docker exec -i kzph-stand python - < /tmp/$q.py; done > $O/db.txt 2>&1
date -u > $O/done
