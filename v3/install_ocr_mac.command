#!/bin/bash
# SibDoc: распознавание сканов (Tesseract) без Homebrew.
# Ставит готовую сборку tesseract из conda-forge в папку .ocr рядом с программой:
# ~200 МБ скачивания и пара минут вместо часов сборки Homebrew на Mac с Intel.
# Запускается из install_mac.command (шаг 3) или отдельно: bash install_ocr_mac.command
set -e
cd "$(dirname "$0")"
DEST="$PWD/.ocr"
TMP="$PWD/.ocr-tmp"

if [ -x "$DEST/bin/tesseract" ] && "$DEST/bin/tesseract" --version >/dev/null 2>&1; then
    echo "      Tesseract уже установлен: $DEST/bin/tesseract"
    exit 0
fi

case "$(uname -s)-$(uname -m)" in
    Darwin-arm64)  PLAT=osx-arm64 ;;
    Darwin-x86_64) PLAT=osx-64 ;;
    Linux-x86_64)  PLAT=linux-64 ;;
    Linux-aarch64) PLAT=linux-aarch64 ;;
    *) echo "      Эта система не поддерживается: $(uname -s) $(uname -m)"; exit 1 ;;
esac

rm -rf "$TMP" "$DEST"
mkdir -p "$TMP"
trap 'rm -rf "$TMP"' EXIT

# 1. micromamba — один файл, ставит пакеты conda-forge без Python и без прав администратора
echo "      Скачиваю установщик пакетов (micromamba, ~15 МБ)…"
MM="$TMP/micromamba"
if ! curl -fsSL --retry 5 --connect-timeout 20 -o "$MM" \
        "https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-$PLAT"; then
    curl -fsSL --retry 5 --connect-timeout 20 "https://micro.mamba.pm/api/micromamba/$PLAT/latest" \
        | tar -xjf - -C "$TMP" bin/micromamba
    mv "$TMP/bin/micromamba" "$MM"
fi
chmod +x "$MM"

# 2. Самый быстрый источник conda-forge: основной или зеркала (Китай, медленные сети)
CH=""; BEST=0
for c in https://conda.anaconda.org/conda-forge \
         https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge \
         https://mirrors.bfsu.edu.cn/anaconda/cloud/conda-forge \
         https://mirrors.aliyun.com/anaconda/cloud/conda-forge; do
    r=$(curl -sL -m 12 -r 0-1999999 -o /dev/null -w '%{http_code} %{speed_download}' "$c/$PLAT/repodata.json" || true)
    code=${r%% *}; speed=${r##* }; speed=${speed%.*}
    if [ "$code" = "200" ] || [ "$code" = "206" ]; then
        if [ "${speed:-0}" -gt "$BEST" ]; then BEST=$speed; CH=$c; fi
    fi
done
[ -n "$CH" ] || { echo "      Нет связи с conda-forge и его зеркалами — проверьте интернет и повторите."; exit 1; }
echo "      Источник: $CH ($((BEST / 1024)) КБ/с)"

# 3. tesseract со всеми библиотеками — в .ocr; кэш пакетов — во временной папке (удаляется)
echo "      Скачиваю и ставлю Tesseract (~200 МБ, несколько минут)…"
export MAMBA_ROOT_PREFIX="$TMP/root" MAMBA_REMOTE_MAX_RETRIES=10 MAMBA_REMOTE_CONNECT_TIMEOUT_SECS=30
"$MM" create -y -q -p "$DEST" --override-channels -c "$CH" tesseract >/dev/null

# языки берутся из папки tessdata программы (rus, eng, chi_sim); свои языки сборки не нужны
find "$DEST/share/tessdata" -maxdepth 1 -name '*.traineddata' ! -name eng.traineddata ! -name osd.traineddata \
    -delete 2>/dev/null || true

"$DEST/bin/tesseract" --version 2>&1 | head -1 | sed 's/^/      Готово: /'
LANGS=$("$DEST/bin/tesseract" --list-langs --tessdata-dir "$PWD/tessdata" 2>/dev/null | tail -n +2 | tr '\n' ' ')
echo "      Языки распознавания: $LANGS"
