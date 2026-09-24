# Образ Playwright той же версии, что и пакет в requirements.txt: раньше здесь был 1.49 при
# pip-Playwright 1.63, и системные библиотеки браузера расходились с библиотекой, которая их зовёт.
# resolute — Ubuntu 26.04 LTS, штатный Python 3.14: та же версия, что в CI и в локальном venv.
FROM mcr.microsoft.com/playwright/python:v1.63.0-resolute

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/app/data \
    ALLOW_DEV_LOGIN=0

WORKDIR /app

COPY requirements.txt constraints.txt ./
# Точные версии: сборка не подтягивает новые релизы зависимостей сама по себе (M11).
# --break-system-packages: Ubuntu 26.04 помечает системный Python как externally-managed (PEP 668).
# Контейнер и есть окружение приложения, отдельный venv тут ничего не изолирует, а CMD и HEALTHCHECK
# зовут системный python3 напрямую.
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt -c constraints.txt && \
    playwright install chromium

COPY . .

RUN mkdir -p /app/data

ENV PORT=35539
EXPOSE 35539

# Состояние контейнера видно в Docker/Container Manager: отвечает ли веб-сервер
HEALTHCHECK --interval=60s --timeout=10s --start-period=120s --retries=3 \
    CMD python3 -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/version' % os.environ.get('PORT', '35539'), timeout=5)" || exit 1

CMD ["python3", "gui.py"]
