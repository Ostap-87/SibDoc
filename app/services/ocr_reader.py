from __future__ import annotations
# -*- coding: utf-8 -*-
"""OCR-читалка сканированных деклараций (PDF/JPG/PNG/TIFF/BMP).

Поддерживает русский, английский и китайский (упрощённый) текст
одновременно: tesseract вызывается с lang=rus+eng+chi_sim.

Логика:
  PDF  -> PyMuPDF рендерит страницы в 300 DPI -> OCR каждой страницы
  image-> предобработка (grayscale + autocontrast) -> OCR

Языковые модели ищутся в:
  1) TESSDATA_PREFIX (если задан)
  2) <project>/tessdata          (поставляется в пакете)
  3) системный tessdata
"""
import io
import os
import shutil
import subprocess

import pytesseract
from PIL import Image, ImageOps

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LOCAL_TESSDATA = os.path.join(PROJECT_ROOT, "tessdata")

LANGS = "rus+eng+chi_sim"


def _tessdata_dir() -> str | None:
    if os.environ.get("TESSDATA_PREFIX"):
        return os.environ["TESSDATA_PREFIX"]
    if os.path.isdir(LOCAL_TESSDATA) and os.listdir(LOCAL_TESSDATA):
        return LOCAL_TESSDATA
    return None


def available_languages() -> list[str]:
    """Какие языки реально доступны OCR (для отображения в UI/API)."""
    cfg = ""
    td = _tessdata_dir()
    if td:
        cfg = f'--tessdata-dir "{td}"'
    try:
        out = pytesseract.get_languages(config=cfg)
        have = set(out)
    except Exception:
        have = {"eng"}
    want = ["rus", "eng", "chi_sim"]
    return [l for l in want if l in have] or ["eng"]


def _ocr_image(img: Image.Image) -> str:
    gray = ImageOps.grayscale(img)
    gray = ImageOps.autocontrast(gray, cutoff=1)
    cfg = ""
    td = _tessdata_dir()
    if td:
        cfg = f'--tessdata-dir "{td}"'
    langs = "+".join(available_languages())
    return pytesseract.image_to_string(gray, lang=langs, config=cfg + " --psm 6")


def _render_pdf(fileobj) -> list[Image.Image]:
    import pymupdf
    doc = pymupdf.open(stream=fileobj.read(), filetype="pdf")
    pages = []
    for page in doc:
        pix = page.get_pixmap(dpi=300)
        pages.append(Image.open(io.BytesIO(pix.tobytes("png"))))
    doc.close()
    return pages


def tesseract_available() -> bool:
    """Установлен ли системный бинарник tesseract."""
    return shutil.which("tesseract") is not None


def read_scan(fileobj, filename: str) -> dict:
    """Прочитать скан -> {text, pages, engine_langs}."""
    if not tesseract_available():
        raise RuntimeError(
            "Tesseract OCR не найден в системе / Tesseract OCR not found / 未找到Tesseract OCR. "
            "Установите tesseract-ocr (см. README.md §OCR) — остальные функции работают без него.")
    low = filename.lower()
    if low.endswith(".pdf"):
        pages = _render_pdf(fileobj)
    else:
        pages = [Image.open(fileobj)]

    texts = []
    for img in pages:
        # ограничим размер, чтобы OCR не умирал на гигантских сканах
        if max(img.size) > 4000:
            ratio = 4000 / max(img.size)
            img = img.resize((int(img.width * ratio), int(img.height * ratio)))
        texts.append(_ocr_image(img))

    return {
        "text": "\n\n".join(texts),
        "pages": len(pages),
        "engine_langs": available_languages(),
    }
