#!/usr/bin/env bash
# SibDoc3: закрытый контур — комплект для установки без интернета (Linux / Mac).
# Запускать на компьютере С ИНТЕРНЕТОМ. Все параметры — как у tools/make_offline_bundle.py:
#   bash tools/make_offline_bundle.sh                                  # для этого компьютера
#   bash tools/make_offline_bundle.sh --platforms all --python 3.11,3.12 --with-ocr --with-python
#   bash tools/make_offline_bundle.sh --dry-run --platforms windows      # только план
# Результат: dist/sibdoc3-offline-ГГГГММДД/ и архив .zip рядом. Подробно — docs/OFFLINE.md.
set -e
cd "$(dirname "$0")/.."
PY="${PYTHON:-}"
[ -z "$PY" ] && [ -x .venv/bin/python ] && PY=.venv/bin/python
[ -z "$PY" ] && PY=$(command -v python3 || command -v python || true)
[ -z "$PY" ] && { echo "Нужен Python 3.10+ (python3)"; exit 1; }
exec "$PY" tools/make_offline_bundle.py "$@"
