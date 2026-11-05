#!/bin/bash
# SibDoc3: закрытый контур — комплект для установки без интернета (Mac, двойной клик).
# Собирает комплект для этого Mac (и Windows, если нужно — см. tools/make_offline_bundle.sh).
# По умолчанию: библиотеки для этого Mac + Tesseract + установщик Python.
cd "$(dirname "$0")/.."
if [ $# -eq 0 ]; then set -- --platforms native --with-ocr --with-python; fi
bash tools/make_offline_bundle.sh "$@"
status=$?
read -r -p "Нажмите Enter, чтобы закрыть…" _
exit $status
