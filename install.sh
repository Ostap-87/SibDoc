#!/usr/bin/env bash
# ============================================================
#  PostControl Suite — установка и запуск одной командой (Linux)
#
#  Работает БЕЗ интернета: все зависимости лежат в ./vendor.
#  Порядок попыток:
#    1) Docker (предпочтительно)  -> контейнер на порту 8000
#    2) Локальный Python 3.10+    -> venv + uvicorn на порту 8000
# ============================================================
set -e
cd "$(dirname "$0")"

echo "==================================================="
echo " PostControl Suite — установка"
echo "==================================================="

if command -v docker >/dev/null 2>&1; then
    echo "[1/3] Найден Docker — собираю образ..."
    docker build -q -t postcontrol-suite . >/dev/null
    echo "[2/3] Запускаю контейнер..."
    docker rm -f postcontrol >/dev/null 2>&1 || true
    docker run -d -p 8000:8000 \
        -v "$(pwd)/config:/srv/postcontrol/config" \
        -v "$(pwd)/references:/srv/postcontrol/references" \
        -v "$(pwd)/storage:/srv/postcontrol/storage" \
        --name postcontrol --restart unless-stopped \
        postcontrol-suite >/dev/null
    echo "[3/3] Проверяю..."
    sleep 3
    curl -s http://localhost:8000/api/health && echo
    echo
    echo " ГОТОВО. Откройте:  http://localhost:8000"
    echo " Проверка на тестовом задании:  ./verify.sh"
    exit 0
fi

echo "Docker не найден — ставлю локально на Python (офлайн, из ./vendor)..."
PY=$(command -v python3 || command -v python || true)
[ -z "$PY" ] && { echo "ОШИБКА: не найден ни Docker, ни python3."; exit 1; }

echo "[1/3] Создаю виртуальное окружение..."
"$PY" -m venv .venv
. .venv/bin/activate

echo "[2/3] Устанавливаю зависимости (офлайн)..."
pip install -q --no-index --find-links vendor -r requirements.txt

echo "[3/3] Готово."
echo
echo " Запуск:   ./start.sh"
echo " Проверка: ./verify.sh"
echo " После запуска откройте:  http://localhost:8000"
