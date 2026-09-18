FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/app/data \
    ALLOW_DEV_LOGIN=0

WORKDIR /app

COPY requirements.txt constraints.txt ./
# Точные версии: сборка не подтягивает новые релизы зависимостей сама по себе (M11)
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt && \
    playwright install chromium

COPY . .

RUN mkdir -p /app/data

ENV PORT=35539
EXPOSE 35539

# Состояние контейнера видно в Docker/Container Manager: отвечает ли веб-сервер
HEALTHCHECK --interval=60s --timeout=10s --start-period=120s --retries=3 \
    CMD python3 -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/version' % os.environ.get('PORT', '35539'), timeout=5)" || exit 1

CMD ["python3", "gui.py"]
