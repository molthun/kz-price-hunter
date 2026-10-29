#!/usr/bin/env bash
# Включение и выключение локальной модели (P17-A, docs/AI_LOCAL_POLICY.md). Это то, что делают кнопки
# «AI вкл» и «AI выкл» на рабочем столе сервера; gpu_probe.sh зовёт его же автоматически.
#
#   ai_switch.sh off [button|game]   — выгрузить модель из видеопамяти, сервис работает на платных AI
#   ai_switch.sh on  [button|auto]   — загрузить модель и держать её в видеопамяти, сервис снова на локальной
#
# Режим пишется в ai_mode.json каталогов данных прода и стенда — приложение читает его перед каждым вызовом.
set -euo pipefail

TARGETS="${TARGETS:-$HOME/kzph-prod/data $HOME/kzph-stand/stand-data}"
OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"
MODEL="${MODEL:-qwen3-vl:8b-instruct}"
action="${1:-}"
by="${2:-button}"
[ "$action" = on ] || [ "$action" = off ] || { echo "использование: $0 on|off [button|game|auto]" >&2; exit 2; }

now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
state=$(python3 -c 'import json, sys; print(json.dumps({"mode": sys.argv[1], "since": sys.argv[2], "by": sys.argv[3]}))' \
  "$action" "$now" "$by")
for dir in $TARGETS; do
  [ -d "$dir" ] || continue
  printf '%s\n' "$state" > "$dir/.ai_mode.json.tmp" && mv -f "$dir/.ai_mode.json.tmp" "$dir/ai_mode.json"
done

if [ "$action" = off ]; then
  # Освободить видеопамять для игры: выгрузить все загруженные модели
  for loaded in $(curl -fsS -m 5 "$OLLAMA_URL/api/ps" 2>/dev/null | python3 -c \
      'import json, sys; print(" ".join(m["name"] for m in json.load(sys.stdin).get("models", [])))' 2>/dev/null || true); do
    curl -fsS -m 15 "$OLLAMA_URL/api/generate" -d "{\"model\": \"$loaded\", \"keep_alive\": 0}" >/dev/null 2>&1 || true
  done
  message="AI выключен: видеокарта свободна, сервис работает на платных моделях"
else
  # Загрузить модель сразу и держать постоянно (keep_alive -1): первый запрос не ждёт загрузки
  curl -fsS -m 120 "$OLLAMA_URL/api/generate" -d "{\"model\": \"$MODEL\", \"keep_alive\": -1}" >/dev/null 2>&1 \
    && message="AI включён: модель $MODEL загружена, сервис снова на локальной модели" \
    || message="AI включён, но модель $MODEL не загрузилась — сервис пока работает на платных"
fi

echo "$(date '+%F %T') $message ($by)"
# Уведомление на рабочем столе, если запуск с кнопки
if [ "$by" = button ] && command -v notify-send >/dev/null 2>&1; then
  notify-send "KZ Price Hunter" "$message" 2>/dev/null || true
fi
