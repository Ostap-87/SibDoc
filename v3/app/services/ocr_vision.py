from __future__ import annotations
# -*- coding: utf-8 -*-
"""OCR через системный Vision (macOS) — резервный движок, если Tesseract не установлен.

Зачем: Tesseract — внешний бинарник (~200 МБ) и его нет в комплекте. На macOS распознавание
уже есть в системе: Vision умеет русский, английский и упрощённый китайский, работает
локально (без интернета) и не требует установки. На Windows и Linux остаётся Tesseract.

Требует pyobjc-framework-Vision (в офлайн-комплект ставится как зависимость на macOS):
    pip install pyobjc-framework-Vision pyobjc-framework-Quartz

Публичный API такой же, как у tesseract-ветки:
    available() -> bool
    languages() -> list[str]
    image_to_string(img) -> str
"""
import io
import sys

LANGS = ["ru-RU", "en-US", "zh-Hans"]     # порядок важен: первый — приоритетный
_CACHE = {}


def _imports():
    """Ленивая загрузка Vision/Quartz: на не-macOS просто вернёт None."""
    if "vision" in _CACHE:
        return _CACHE["vision"]
    mod = None
    if sys.platform == "darwin":
        try:
            import Quartz  # noqa: F401
            import Vision
            mod = Vision
        except Exception:  # noqa: BLE001
            mod = None
    _CACHE["vision"] = mod
    return mod


def available() -> bool:
    """Есть ли системный OCR-движок."""
    return _imports() is not None


def languages() -> list[str]:
    return ["rus", "eng", "chi_sim"] if available() else []


def _make_request(vision):
    """Запрос на распознавание: точный режим, авто-язык, подсказка ru/en/zh."""
    request = vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(0)              # 0 = accurate
    request.setUsesLanguageCorrection_(False)    # цифры и коды важнее «красивых» слов
    try:
        request.setAutomaticallyDetectsLanguage_(True)   # смешанные документы RU + 中文
    except Exception:  # noqa: BLE001
        pass
    try:
        request.setRecognitionLanguages_(LANGS)
    except Exception:  # noqa: BLE001
        pass
    return request


def _prepare(img):
    """Мелкие сканы Vision читает хуже — увеличиваем и повышаем контраст."""
    from PIL import ImageOps
    img = img.convert("RGB")
    if max(img.size) < 1600:
        k = 2 if max(img.size) < 1000 else 1.5
        img = img.resize((int(img.width * k), int(img.height * k)), Image.LANCZOS)
    gray = ImageOps.autocontrast(ImageOps.grayscale(img), cutoff=1)
    return gray.convert("RGB")


def image_to_lines(img) -> list[tuple[str, float]]:
    """Распознать картинку -> [(строка, уверенность 0…1)] — как image_to_data у Tesseract."""
    vision = _imports()
    if vision is None:
        raise RuntimeError("Vision OCR недоступен (не macOS или нет pyobjc-framework-Vision)")
    from Foundation import NSData

    buf = io.BytesIO()
    _prepare(img).save(buf, format="PNG")
    raw = buf.getvalue()
    data = NSData.dataWithBytes_length_(raw, len(raw))

    handler = vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)
    request = _make_request(vision)
    ok, err = handler.performRequests_error_([request], None)
    if not ok:
        raise RuntimeError(f"Vision OCR: {err}")

    out = []
    for obs in (request.results() or []):
        cands = obs.topCandidates_(1)
        if not cands or not len(cands):
            continue
        c = cands[0]
        try:
            conf = float(c.confidence())
        except Exception:  # noqa: BLE001
            conf = 0.5
        out.append((str(c.string()), round(max(0.0, min(1.0, conf)), 3)))
    return out


def image_to_string(img) -> str:
    """Распознать PIL-картинку. Возвращает текст (строки через перевод строки)."""
    vision = _imports()
    if vision is None:
        raise RuntimeError("Vision OCR недоступен (не macOS или нет pyobjc-framework-Vision)")
    from Foundation import NSData

    buf = io.BytesIO()
    _prepare(img).save(buf, format="PNG")
    raw = buf.getvalue()
    data = NSData.dataWithBytes_length_(raw, len(raw))

    handler = vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)
    request = _make_request(vision)
    request.setRecognitionLevel_(0)             # 0 = accurate, 1 = fast
    request.setUsesLanguageCorrection_(False)   # цифры и коды важнее «красивых» слов
    try:
        request.setRecognitionLanguages_(LANGS)
    except Exception:  # noqa: BLE001
        pass

    ok, err = handler.performRequests_error_([request], None)
    if not ok:
        raise RuntimeError(f"Vision OCR: {err}")

    lines = []
    for obs in (request.results() or []):
        cands = obs.topCandidates_(1)
        if cands and len(cands):
            lines.append(str(cands[0].string()))
    return "\n".join(lines)
