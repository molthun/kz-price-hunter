#!/usr/bin/env bash
# Проверка видеокарты для локальной модели (P17-A, docs/AI_LOCAL_POLICY.md). Запускается cron хоста раз в минуту:
#
#   * * * * * /home/molthun/kzph-prod/gpu_probe.sh >> /home/molthun/kzph-prod/gpu_probe.log 2>&1
#
# Компьютер семейный. Скрипт:
# - выключает локальную модель сам (ai_switch.sh off game), если видеокарту заняла игра, а «AI выкл» не нажали;
# - включает обратно (ai_switch.sh on auto) через RESUME_AFTER_MIN после выключения — кнопкой или игрой, —
#   если видеокарта уже FREE_MIN минут не занята чужой программой;
# - пока включено, держит модель загруженной (после перезагрузки машины или Ollama загружает заново);
# - пишет gpu_state.json в каталоги данных прода и стенда — для мониторинга в админке.
# В журнал пишутся только действия.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
TARGETS="${TARGETS:-$HOME/kzph-prod/data $HOME/kzph-stand/stand-data}"
OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"
NVIDIA_SMI="${NVIDIA_SMI:-nvidia-smi}"
SWITCH="${SWITCH:-$HERE/ai_switch.sh}"
MODEL="${MODEL:-qwen3-vl:8b-instruct}"
# Рабочий стол, браузер и удалённый доступ держат на видеокарте несколько сотен МБ (замер 29.10.2026: 427 МБ)
BASELINE_MB="${BASELINE_MB:-600}"
# Больше этого чужой памяти — значит, запущена игра или другая тяжёлая программа
FOREIGN_MB="${FOREIGN_MB:-1500}"
# Загрузка видеокарты, когда модель Ollama не загружена, — тоже признак чужой работы
FOREIGN_UTIL="${FOREIGN_UTIL:-50}"
RESUME_AFTER_MIN="${RESUME_AFTER_MIN:-60}"
FREE_MIN="${FREE_MIN:-10}"

now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
first_target="${TARGETS%% *}"
export TARGETS OLLAMA_URL MODEL

read -r util used <<<"$("$NVIDIA_SMI" --query-gpu=utilization.gpu,memory.used --format=csv,noheader,nounits | head -1 | tr -d ' ' | tr ',' ' ')"
ollama_mb=$("$NVIDIA_SMI" --query-compute-apps=process_name,used_memory --format=csv,noheader,nounits \
  | awk -F', *' 'tolower($1) ~ /ollama/ {s += $2} END {print s + 0}')
foreign=$(( used - ollama_mb - BASELINE_MB ))
[ "$foreign" -lt 0 ] && foreign=0

busy=0
reason=""
if [ "$foreign" -gt "$FOREIGN_MB" ]; then
  busy=1; reason="чужая программа занимает ${foreign} МБ видеопамяти"
elif [ "$ollama_mb" -eq 0 ] && [ "$util" -ge "$FOREIGN_UTIL" ]; then
  busy=1; reason="видеокарта загружена на ${util}% без модели Ollama"
fi

# Режим и прошлая отметка: включено ли, сколько минут с переключения, сколько минут видеокарта не занята
facts=$(python3 -c '
import datetime, json, sys
def load(path):
    try:
        return json.load(open(path))
    except Exception:
        return {}
def minutes(value):
    try:
        at = datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
        return int((datetime.datetime.now(datetime.timezone.utc) - at).total_seconds() // 60)
    except Exception:
        return -1
mode, gpu = load(sys.argv[1]), load(sys.argv[2])
last_busy = gpu.get("last_busy_at") or ""
print(mode.get("mode") or "on", minutes(mode.get("since") or ""), last_busy or "-", minutes(last_busy))
' "$first_target/ai_mode.json" "$first_target/gpu_state.json" 2>/dev/null || echo "on -1 - -1")
read -r mode since_min last_busy_at free_for_min <<<"$facts"
[ "$last_busy_at" = "-" ] && last_busy_at=""
if [ "$busy" = 1 ]; then
  last_busy_at="$now"
  free_for_min=0
fi

if [ "$mode" = on ] && [ "$busy" = 1 ]; then
  "$SWITCH" off game
  mode=off
elif [ "$mode" = off ] && [ "$busy" = 0 ] && [ "$since_min" -ge "$RESUME_AFTER_MIN" ] \
     && { [ -z "$last_busy_at" ] || [ "$free_for_min" -ge "$FREE_MIN" ]; }; then
  "$SWITCH" on auto
  mode=on
elif [ "$mode" = on ] && [ "$ollama_mb" -eq 0 ] && [ "$busy" = 0 ]; then
  # Включено, но модели нет в видеопамяти (перезагрузка машины или Ollama) — загрузить, не меняя режим
  if curl -fsS -m 120 "$OLLAMA_URL/api/generate" -d "{\"model\": \"$MODEL\", \"keep_alive\": -1}" >/dev/null 2>&1; then
    echo "$(date '+%F %T') модель $MODEL загружена заново: её не было в видеопамяти"
  fi
fi

state=$(python3 -c "import json, sys
print(json.dumps({'at': sys.argv[1], 'busy': sys.argv[2] == '1', 'reason': sys.argv[3], 'utilization': int(sys.argv[4]),
                  'used_mb': int(sys.argv[5]), 'ollama_mb': int(sys.argv[6]), 'foreign_mb': int(sys.argv[7]),
                  'last_busy_at': sys.argv[8] or None, 'mode': sys.argv[9]}, ensure_ascii=False))" \
  "$now" "$busy" "$reason" "$util" "$used" "$ollama_mb" "$foreign" "${last_busy_at:-}" "$mode")
for dir in $TARGETS; do
  [ -d "$dir" ] || continue
  printf '%s\n' "$state" > "$dir/.gpu_state.json.tmp" && mv -f "$dir/.gpu_state.json.tmp" "$dir/gpu_state.json"
done
