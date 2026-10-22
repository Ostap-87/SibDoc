from __future__ import annotations
# -*- coding: utf-8 -*-
"""OCR-читалка сканированных деклараций (PDF/JPG/PNG/TIFF/BMP).

Поддерживает русский, английский и китайский (упрощённый) текст
одновременно: tesseract вызывается с lang=rus+eng+chi_sim.

Логика:
  PDF  -> если на странице есть текстовый слой (PDF из программы, а не скан) —
          берём готовый текст без OCR; иначе PyMuPDF рендерит страницу в 300 DPI -> OCR
  TIFF -> все страницы многостраничного файла
  image-> предобработка (grayscale + autocontrast) -> OCR

Языковые модели ищутся в:
  1) TESSDATA_PREFIX (если задан)
  2) <project>/tessdata          (поставляется в пакете)
  3) системный tessdata

Сама программа tesseract ищется в:
  1) SIBDOC_TESSERACT (путь к бинарнику, если задан)
  2) <project>/.ocr/bin/tesseract  (ставит install_ocr_mac.command, сборка conda-forge)
  3) PATH и обычные места Homebrew / MacPorts / Windows — у приложения, открытого из Finder,
     PATH урезан (/usr/bin:/bin), поэтому /opt/homebrew/bin и /usr/local/bin проверяются явно
"""
import io
import os
import shutil
import subprocess

import pytesseract
from PIL import Image, ImageOps, ImageSequence

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LOCAL_TESSDATA = os.path.join(PROJECT_ROOT, "tessdata")

LANGS = "rus+eng+chi_sim"

TESSERACT_PLACES = (
    os.path.join(PROJECT_ROOT, ".ocr", "bin", "tesseract"),
    os.path.join(PROJECT_ROOT, ".ocr", "Library", "bin", "tesseract.exe"),
    "/opt/homebrew/bin/tesseract",
    "/usr/local/bin/tesseract",
    "/opt/local/bin/tesseract",
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
)


def tesseract_cmd() -> str | None:
    """Путь к бинарнику tesseract (см. порядок поиска выше) или None."""
    env = os.environ.get("SIBDOC_TESSERACT")
    if env and os.path.isfile(env):
        return env
    places = list(TESSERACT_PLACES[:2]) + [shutil.which("tesseract")] + list(TESSERACT_PLACES[2:])
    for p in places:
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def _use_tesseract() -> None:
    cmd = tesseract_cmd()
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd


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
    _use_tesseract()
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


MIN_TEXT_LAYER_CHARS = 40


def _pdf_pages(fileobj) -> list:
    """-> список страниц: str (готовый текст слоя) или Image (нужен OCR)."""
    import pymupdf
    doc = pymupdf.open(stream=fileobj.read(), filetype="pdf")
    pages = []
    for page in doc:
        text = page.get_text("text") or ""
        if len("".join(text.split())) >= MIN_TEXT_LAYER_CHARS:
            pages.append(text)
            continue
        pix = page.get_pixmap(dpi=300)
        pages.append(Image.open(io.BytesIO(pix.tobytes("png"))))
    doc.close()
    return pages


def _image_pages(fileobj) -> list:
    img = Image.open(fileobj)
    frames = [f.copy() for f in ImageSequence.Iterator(img)]
    return frames or [img]


def tesseract_available() -> bool:
    """Есть ли бинарник tesseract (в .ocr программы, в PATH или в обычных местах)."""
    return tesseract_cmd() is not None


def read_scan(fileobj, filename: str) -> dict:
    """Прочитать скан -> {text, pages, engine_langs}."""
    low = filename.lower()
    pages = _pdf_pages(fileobj) if low.endswith(".pdf") else _image_pages(fileobj)
    need_ocr = any(not isinstance(p, str) for p in pages)
    if need_ocr and not tesseract_available():
        raise RuntimeError(
            "Tesseract OCR не найден в системе / Tesseract OCR not found / 未找到Tesseract OCR. "
            "Установите его: Mac — bash install_ocr_mac.command, остальные — README.md §OCR. "
            "Остальные функции работают без него.")

    texts, text_layer = [], 0
    for img in pages:
        if isinstance(img, str):
            texts.append(img)
            text_layer += 1
            continue
        # ограничим размер, чтобы OCR не умирал на гигантских сканах
        if max(img.size) > 4000:
            ratio = 4000 / max(img.size)
            img = img.resize((int(img.width * ratio), int(img.height * ratio)))
        texts.append(_ocr_image(img))

    return {
        "text": "\n\n".join(texts),
        "pages": len(pages),
        "text_layer_pages": text_layer,
        "engine_langs": available_languages() if need_ocr else ["text-layer"],
    }
