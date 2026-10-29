#!/usr/bin/env bash
# Кнопки «AI вкл» и «AI выкл» на рабочем столе сервера и проверка видеокарты в cron (P17-A,
# docs/AI_LOCAL_POLICY.md). Запустить один раз на машине прода из ~/kzph-prod. Повторный запуск безопасен.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
desktop=$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")
mkdir -p "$desktop"
chmod 755 "$HERE/ai_switch.sh" "$HERE/gpu_probe.sh"

button() {  # button файл имя действие значок пояснение
  cat > "$desktop/$1" <<EOF
[Desktop Entry]
Type=Application
Name=$2
Comment=$5
Exec=bash -c '"$HERE/ai_switch.sh" $3 button >> "$HERE/ai_mode.log" 2>&1'
Icon=$4
Terminal=false
EOF
  chmod 755 "$desktop/$1"
  # GNOME запускает ярлык с рабочего стола только после отметки «доверять»
  gio set "$desktop/$1" metadata::trusted true 2>/dev/null || true
}
button kzph-ai-off.desktop "AI выкл" off media-playback-pause \
  "Освободить видеокарту для игр: сервис перейдёт на платные AI, через час без игр включится сам"
button kzph-ai-on.desktop "AI вкл" on media-playback-start \
  "Вернуть локальную модель: загрузить её в видеопамять и держать постоянно"

line="* * * * * $HERE/gpu_probe.sh >> $HERE/gpu_probe.log 2>&1"
( crontab -l 2>/dev/null | grep -v "gpu_probe.sh" || true; echo "$line" ) | crontab -
echo "Кнопки: $desktop/kzph-ai-on.desktop и kzph-ai-off.desktop; cron: $line"
