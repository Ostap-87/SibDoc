#!/bin/bash
# ============================================================
#  SibDoc — установка на Mac (macOS 11+, Apple Silicon или Intel)
#  Запуск: двойной клик по файлу или в Терминале: bash install_mac.command
#  Что делает: проверяет Python 3.11+, ставит зависимости в папку .venv,
#  ставит Tesseract для сканов (.ocr), создаёт приложение «SibDoc» в ~/Applications
#  и значок на рабочем столе. Повторный запуск обновляет установку.
# ============================================================
set -e
cd "$(dirname "$0")"
APP_DIR="$(pwd)"
echo "=== SibDoc: установка в $APP_DIR ==="

PY=""
for c in python3.13 python3.12 python3.11 /opt/homebrew/bin/python3 /usr/local/bin/python3 /Library/Frameworks/Python.framework/Versions/Current/bin/python3 python3; do
    if command -v "$c" >/dev/null 2>&1; then
        ok=$("$c" -c 'import sys; print(sys.version_info >= (3, 11))' 2>/dev/null || echo False)
        if [ "$ok" = "True" ]; then PY="$(command -v "$c")"; break; fi
    fi
done
if [ -z "$PY" ]; then
    echo
    echo "Нужен Python 3.11 или новее. Сейчас откроется python.org:"
    echo "скачайте «macOS 64-bit universal2 installer», установите и запустите этот файл ещё раз."
    open "https://www.python.org/downloads/macos/"
    read -r -p "Нажмите Enter, чтобы закрыть…" _
    exit 1
fi
echo "[1/4] Python: $PY ($("$PY" --version))"

echo "[2/4] Устанавливаю зависимости (несколько минут, нужен интернет)…"
"$PY" -m venv .venv
# Медленный или недоступный pypi.org (Китай, часть сетей в России) — ставим с зеркала.
# Своё зеркало можно задать заранее: PIP_INDEX_URL=… bash install_mac.command
if [ -z "$PIP_INDEX_URL" ]; then
    if ! curl -s -m 8 -o /dev/null https://pypi.org/simple/pip/; then
        for m in https://pypi.tuna.tsinghua.edu.cn/simple https://mirrors.aliyun.com/pypi/simple/; do
            if curl -s -m 8 -o /dev/null "$m/pip/"; then export PIP_INDEX_URL="$m"; break; fi
        done
        [ -n "$PIP_INDEX_URL" ] && echo "      pypi.org отвечает медленно — использую зеркало $PIP_INDEX_URL"
    fi
fi
export PIP_DEFAULT_TIMEOUT="${PIP_DEFAULT_TIMEOUT:-60}"
./.venv/bin/python -m pip install -q --timeout 60 --retries 10 --upgrade pip
./.venv/bin/python -m pip install -q --timeout 60 --retries 10 -r requirements.txt

echo "[3/4] Распознавание сканов деклараций (Tesseract)"
# Готовая сборка из conda-forge в папку .ocr (без Homebrew: на Mac с Intel он собирает tesseract часами).
if ! bash "$APP_DIR/install_ocr_mac.command"; then
    echo "      Не получилось скачать Tesseract — всё остальное работает. Повторить: bash install_ocr_mac.command"
fi

echo "[4/4] Создаю приложение SibDoc в ~/Applications"
APP="$HOME/Applications/SibDoc.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$HOME/Library/Logs"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>SibDoc</string>
  <key>CFBundleDisplayName</key><string>SibDoc</string>
  <key>CFBundleIdentifier</key><string>ru.sibdoc.desktop</string>
  <key>CFBundleVersion</key><string>2.0</string>
  <key>CFBundleShortVersionString</key><string>2.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>SibDoc</string>
  <key>CFBundleIconFile</key><string>SibDoc</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
cat > "$APP/Contents/MacOS/SibDoc" <<LAUNCH
#!/bin/bash
cd "$APP_DIR"
exec "$APP_DIR/.venv/bin/python" "$APP_DIR/sibdoc_gui.py" >> "\$HOME/Library/Logs/SibDoc.log" 2>&1
LAUNCH
chmod +x "$APP/Contents/MacOS/SibDoc"
cp "$APP_DIR/assets/SibDoc.icns" "$APP/Contents/Resources/SibDoc.icns"
touch "$APP"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP" 2>/dev/null || true
# значок на рабочем столе (ссылка на приложение)
ln -sfn "$APP" "$HOME/Desktop/SibDoc"
# если SibDoc уже открыт — закрываем, чтобы запустился обновлённый
pkill -f "$APP_DIR/sibdoc_gui.py" 2>/dev/null || true

echo
echo "ГОТОВО. Значок «SibDoc» — на рабочем столе; само приложение — в папке «Программы» вашего"
echo "пользователя (~/Applications). Чтобы держать под рукой, перетащите значок в Dock."
echo "Не переносите и не удаляйте папку $APP_DIR — приложение запускается из неё."
open "$APP"
