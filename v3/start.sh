#!/usr/bin/env bash
# Запуск PostControl Suite (после install.sh)
cd "$(dirname "$0")"

if docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^postcontrol$'; then
    echo "Контейнер уже работает: http://localhost:8000"
    exit 0
fi
if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q '^postcontrol$'; then
    docker start postcontrol >/dev/null
    echo "Контейнер запущен: http://localhost:8000"
    exit 0
fi

[ -f .venv/bin/activate ] && . .venv/bin/activate
echo "Старт API+UI на http://localhost:8000  (Ctrl+C — остановить)"
exec uvicorn app.server:app --host 0.0.0.0 --port 8000
