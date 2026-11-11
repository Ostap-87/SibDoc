#!/bin/bash
# Скачать Tesseract и языковые пакеты для Linux-контура (Debian/Ubuntu) в offline/ocr/linux.
# Запускать на машине С интернетом и apt; получившуюся папку перенести в комплект.
#   bash tools/fetch_tesseract_linux.sh [папка]     (по умолчанию offline/ocr/linux)
set -e
OUT="${1:-offline/ocr/linux}"
mkdir -p "$OUT"
cd "$OUT"
echo "Скачиваю Tesseract для Linux в $OUT ..."
if command -v apt-get >/dev/null 2>&1; then
    apt-get download tesseract-ocr liblept5 libtesseract5 \
        tesseract-ocr-rus tesseract-ocr-eng tesseract-ocr-chi-sim 2>/dev/null || \
    apt-get download tesseract-ocr liblept5 libtesseract5 \
        tesseract-ocr-rus tesseract-ocr-eng 2>/dev/null || true
else
    echo "Нет apt-get. На Debian/Ubuntu нужен apt; на других дистрибутивах поставьте пакет tesseract вручную."
    exit 1
fi
ls -1 *.deb 2>/dev/null | wc -l | xargs -I{} echo "Скачано пакетов: {}"
cat > install_tesseract_linux.sh <<'EOS'
#!/bin/bash
# Установка Tesseract из папки (без интернета): bash install_tesseract_linux.sh
set -e
cd "$(dirname "$0")"
sudo dpkg -i ./*.deb || sudo apt-get -f install -y
tesseract --version | head -1
EOS
chmod +x install_tesseract_linux.sh
echo "Готово. Для установки в контуре: bash install_tesseract_linux.sh"
