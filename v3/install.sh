#!/usr/bin/env bash
# ============================================================
#  PostControl Suite — установка и запуск одной командой (Linux)
#
#  Порядок попыток:
#    1) Docker (предпочтительно)  -> контейнер на порту 8000
#    2) Локальный Python 3.10+    -> venv + uvicorn на порту 8000
#
#  SibDoc3: закрытый контур — установка БЕЗ интернета из комплекта
#  (tools/make_offline_bundle.py, docs/OFFLINE.md):
#    bash install.sh --offline wheelhouse      # папка с библиотеками *.whl
#    SIBDOC_OFFLINE_DIR=/media/usb/wheelhouse bash install.sh
#  Папка ./wheelhouse (или старая ./vendor) рядом находится сама. Docker в
#  закрытом контуре не собирается: образ берётся из offline/docker/*.tar.gz
#  (docker load) или уже загруженный. SIBDOC_NO_DOCKER=1 — не использовать Docker.
# ============================================================
set -e
cd "$(dirname "$0")"

# ---- SibDoc3: закрытый контур — параметры
OFFDIR="${SIBDOC_OFFLINE_DIR:-}"
while [ $# -gt 0 ]; do
    case "$1" in
        --offline)
            shift
            if [ $# -gt 0 ] && [ "${1#--}" = "$1" ]; then OFFDIR="$1"; shift; else OFFDIR="wheelhouse"; fi ;;
        --offline=*) OFFDIR="${1#--offline=}"; shift ;;
        -h|--help)
            echo "bash install.sh [--offline ПАПКА_С_WHL]   (подробно — docs/OFFLINE.md)"; exit 0 ;;
        *) echo "Неизвестный параметр: $1 (есть только --offline ПАПКА)"; exit 2 ;;
    esac
done
has_wheels() { [ -d "$1" ] && [ -n "$(find "$1" -maxdepth 2 -name '*.whl' 2>/dev/null | head -1)" ]; }
if [ -z "$OFFDIR" ]; then
    if has_wheels wheelhouse; then OFFDIR=wheelhouse; elif has_wheels vendor; then OFFDIR=vendor; fi
fi
if [ -n "$OFFDIR" ]; then
    has_wheels "$OFFDIR" || { echo "ОШИБКА: в папке «$OFFDIR» нет библиотек *.whl — соберите комплект: tools/make_offline_bundle.sh"; exit 1; }
    export PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1
    if [ -f SHA256SUMS ] && command -v sha256sum >/dev/null 2>&1; then
        sha256sum -c --quiet SHA256SUMS >/dev/null 2>&1 \
            && echo "Комплект проверен: контрольные суммы совпадают (SHA256SUMS)" \
            || echo "ВНИМАНИЕ: часть файлов не совпадает с SHA256SUMS (sha256sum -c SHA256SUMS — список)"
    fi
fi

echo "==================================================="
echo " PostControl Suite — установка${OFFDIR:+ без интернета (из $OFFDIR)}"
echo "==================================================="

run_container() {
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
}

# SibDoc3: Docker — только если он установлен и запущен (docker info), иначе Python
if [ -z "$SIBDOC_NO_DOCKER" ] && command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    if [ -z "$OFFDIR" ]; then
        echo "[1/3] Найден Docker — собираю образ..."
        docker build -q -t postcontrol-suite . >/dev/null
        echo "[2/3] Запускаю контейнер..."
        run_container
        exit 0
    fi
    # SibDoc3: закрытый контур — образ из комплекта, без сборки (сборке нужен интернет)
    IMG=$(ls offline/docker/*.tar.gz offline/docker/*.tar 2>/dev/null | head -1 || true)
    if [ -n "$IMG" ]; then
        echo "[1/3] Загружаю образ Docker из комплекта: $IMG"
        docker load -i "$IMG" >/dev/null
    fi
    if docker image inspect postcontrol-suite >/dev/null 2>&1; then
        echo "[2/3] Запускаю контейнер..."
        run_container
        exit 0
    fi
    echo "Образа Docker в комплекте нет — ставлю на Python."
fi

echo "Ставлю локально на Python..."
PY=$(command -v python3 || command -v python || true)
if [ -z "$PY" ]; then
    echo "ОШИБКА: не найден ни Docker, ни python3."
    [ -n "$OFFDIR" ] && echo "В закрытом контуре: python3 (3.10+) и python3-venv — из дистрибутива (локальный репозиторий / DVD)."
    exit 1
fi

echo "[1/3] Создаю виртуальное окружение..."
"$PY" -m venv .venv
. .venv/bin/activate

if [ -n "$OFFDIR" ]; then
    echo "[2/3] Устанавливаю зависимости без интернета (из $OFFDIR)..."
    LINKS=()
    while IFS= read -r d; do LINKS+=(--find-links "$d"); done < <(
        find "$OFFDIR" -maxdepth 2 -name '*.whl' -print | while IFS= read -r f; do dirname "$f"; done | sort -u)
    pip install -q --no-index "${LINKS[@]}" -r requirements.txt
else
    echo "[2/3] Комплекта библиотек нет — устанавливаю зависимости из интернета..."
    echo "      (для установки без интернета: tools/make_offline_bundle.sh, затем install.sh --offline wheelhouse)"
    pip install -q -r requirements.txt
fi

echo "[3/3] Готово."
echo
echo " Запуск:   ./start.sh"
echo " Проверка: ./verify.sh"
echo " После запуска откройте:  http://localhost:8000"
