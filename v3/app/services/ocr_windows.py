from __future__ import annotations
# -*- coding: utf-8 -*-
"""OCR через встроенный движок Windows (Windows.Media.Ocr) — без Tesseract.

Зачем: Tesseract — внешний установщик (~200 МБ), и в закрытом контуре его может не быть.
В Windows 10/11 распознавание текста уже есть в системе: движок Windows.Media.Ocr умеет
русский, английский и китайский (если установлены языковые пакеты), работает локально.

Доступ идёт через пакет winsdk (pip install winsdk) — он ставится из комплекта.
Языки: смотрим OcrEngine.available_recognizer_languages и берём первый подходящий;
если русского/китайского пакета нет — Windows распознает хотя бы латиницу.

Уверенность: Windows OCR не отдаёт её по строкам, поэтому ставим 0.6 — такие строки
попадают в «требует проверки» (REVIEW), а не выдаются за точное совпадение.
"""
import asyncio
import os
import sys
import tempfile

CONF = 0.6                      # условная уверенность: движок её не сообщает
LANGS_WANTED = ("ru-RU", "zh-Hans", "en-US")
_CACHE = {}


def _available() -> bool:
    """Есть ли winsdk и работаем ли мы на Windows."""
    if "ok" in _CACHE:
        return _CACHE["ok"]
    ok = False
    if sys.platform.startswith("win"):
        try:
            from winsdk.windows.media.ocr import OcrEngine  # noqa: F401
            ok = True
        except Exception:  # noqa: BLE001
            ok = False
    _CACHE["ok"] = ok
    return ok


def available() -> bool:
    return _available()


def languages() -> list[str]:
    """Языки, реально установленные в системе (ru/eng/chi_sim в терминах приложения)."""
    if not _available():
        return []
    try:
        from winsdk.windows.media.ocr import OcrEngine
        have = {lang.language_tag for lang in OcrEngine.available_recognizer_languages}
    except Exception:  # noqa: BLE001
        return ["eng"]
    out = []
    if any(t.lower().startswith("ru") for t in have):
        out.append("rus")
    if any(t.lower().startswith("zh") for t in have):
        out.append("chi_sim")
    out.append("eng")
    return out


async def _recognize(path: str) -> list[str]:
    from winsdk.windows.globalization import Language
    from winsdk.windows.graphics.imaging import BitmapDecoder
    from winsdk.windows.media.ocr import OcrEngine
    from winsdk.windows.storage import FileAccessMode, StorageFile

    engine = None
    try:
        have = {lang.language_tag for lang in OcrEngine.available_recognizer_languages}
    except Exception:  # noqa: BLE001
        have = set()
    for tag in LANGS_WANTED:
        if tag in have:
            engine = OcrEngine.try_create_from_language(Language(tag))
            if engine:
                break
    if engine is None:
        engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        return []

    f = await StorageFile.get_file_from_path_async(path)
    stream = await f.open_async(FileAccessMode.READ)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    result = await engine.recognize_async(bitmap)
    return [str(line.text) for line in (result.lines or [])]


def image_to_lines(img) -> list[tuple[str, float]]:
    """PIL-картинка -> [(строка, уверенность)] в том же виде, что у Tesseract и Vision."""
    if not _available():
        raise RuntimeError("Windows OCR недоступен (не Windows или нет пакета winsdk)")
    fd, path = tempfile.mkstemp(suffix=".png", prefix="sibdoc-ocr-")
    os.close(fd)
    try:
        img.convert("RGB").save(path, format="PNG")
        lines = asyncio.run(_recognize(path))
        return [(t, CONF) for t in lines if t.strip()]
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
