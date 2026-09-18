#!/usr/bin/env bash
# Локальный запуск на этом Mac со входом разработчика.
# Вход работает только на http://localhost:8080 (прямое подключение), не по LAN-адресу.
# Не для сервера: в Docker вход разработчика всегда выключен.
set -euo pipefail
cd "$(dirname "$0")"

# Любая из этих переменных отключает вход разработчика — убираем их из окружения запуска
unset APP_URL PUBLIC_ORIGIN ADMIN_TELEGRAM_IDS TRUSTED_PROXIES
export ALLOW_DEV_LOGIN=1

echo "Вход разработчика: откройте http://localhost:${PORT:-8080}"
exec ./venv/bin/python3 gui.py "$@"
