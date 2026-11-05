from __future__ import annotations
# -*- coding: utf-8 -*-
"""Пакетная загрузка, шаг 1: файлы любого формата -> части (Part) и единицы (Unit).

Part — один файл пакета: загруженный напрямую, извлечённый из ZIP (в том числе
вложенного) или вложение письма. Unit — страница PDF / картинки, лист Excel,
текст документа Word / TXT / XML / письма. Дальше единицы классифицируются
(classify.py), делятся на документы и разбираются на поля (extract.py).

Форматы:
  ZIP (вложенные папки и архивы), PDF (текстовый слой или OCR постранично),
  Excel .xlsx/.xlsm/.xls, CSV/TSV, Word .docx (zipfile + XML, без сторонних
  библиотек), .doc/.rtf/.odt/.ods — через установленный LibreOffice (если есть),
  картинки JPG/PNG/TIFF (многостраничный)/BMP/GIF/WEBP — Tesseract локально,
  XML (ДТ ФТС, 报关单 и любой другой), TXT, HTML, письма EML с вложениями.
Всё работает без сети. Неизвестный формат не роняет пакет: часть получает
статус «unsupported» и попадает в список документов как «не распознан».
"""
import csv
import email
import email.policy
import functools
import html
import io
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Callable

from app.services import ocr_reader, xml_decl
from app.services.loader import _decode_text

EXT_FORMAT = {
    ".pdf": "pdf",
    ".jpg": "image", ".jpeg": "image", ".png": "image", ".tif": "image", ".tiff": "image",
    ".bmp": "image", ".gif": "image", ".webp": "image",
    ".xlsx": "excel", ".xlsm": "excel", ".xls": "excel",
    ".csv": "csv", ".tsv": "csv",
    ".docx": "docx",
    ".doc": "legacy", ".rtf": "legacy", ".odt": "legacy", ".ods": "legacy",
    ".xml": "xml",
    ".txt": "txt", ".text": "txt", ".log": "txt", ".md": "txt",
    ".htm": "html", ".html": "html",
    ".eml": "eml",
    ".zip": "zip",
}
UNSUPPORTED_HINTS = {
    ".rar": "архив RAR не поддерживается — перепакуйте в ZIP",
    ".7z": "архив 7z не поддерживается — перепакуйте в ZIP",
    ".msg": "письмо Outlook .msg не поддерживается — сохраните письмо как .eml",
    ".heic": "фото HEIC не поддерживается — сохраните как JPG",
}
LEGACY_TARGET = {".doc": "docx", ".rtf": "docx", ".odt": "docx", ".ods": "xlsx"}
MIN_TEXT_LAYER_CHARS = 40
W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class FormatError(ValueError):
    """Файл не читается (повреждён, защищён паролем и т. п.)."""


# ------------------------------------------------------------------ структуры
@dataclass
class Line:
    text: str
    conf: float = 1.0          # уверенность OCR 0…1 (для текстового слоя и файлов — 1.0)


@dataclass
class Unit:
    kind: str                  # page | sheet | text | xml
    index: int                 # номер страницы / листа, с 1
    label: str                 # «стр. 2», «лист «Invoice»»
    lines: list = field(default_factory=list)       # list[Line]
    tables: list = field(default_factory=list)      # list[list[list[str]]] — таблицы: строки × ячейки
    method: str = "text"       # text | ocr | table | xml | none
    ocr_conf: float | None = None
    image: str = ""            # картинка страницы для просмотра (путь внутри пакета)
    sheet: str = ""
    xml_root: str = ""
    xml_profile: str = ""
    warnings: list = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(ln.text for ln in self.lines)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "index": self.index, "label": self.label,
                "lines": [[ln.text, round(ln.conf, 3)] for ln in self.lines], "tables": self.tables,
                "method": self.method, "ocr_conf": self.ocr_conf, "image": self.image, "sheet": self.sheet,
                "xml_root": self.xml_root, "xml_profile": self.xml_profile, "warnings": self.warnings}

    @classmethod
    def from_dict(cls, d: dict) -> "Unit":
        u = cls(kind=d.get("kind", "text"), index=int(d.get("index", 1)), label=d.get("label", ""))
        u.lines = [Line(t, float(c)) for t, c in d.get("lines") or []]
        u.tables = d.get("tables") or []
        for k in ("method", "ocr_conf", "image", "sheet", "xml_root", "xml_profile", "warnings"):
            if k in d:
                setattr(u, k, d[k])
        return u


@dataclass
class Part:
    no: int                    # порядковый номер части в пакете
    path: str                  # «Пакет.zip/Инвойсы/inv.xlsx» — откуда взят файл
    name: str                  # имя файла
    fmt: str                   # pdf | image | excel | csv | docx | legacy | xml | txt | html | eml | unknown
    data: bytes = b""
    units: list = field(default_factory=list)       # list[Unit]
    status: str = "ok"         # ok | unsupported | error
    message: str = ""
    container: str = ""        # из какого архива / письма
    stored: str = ""           # путь сохранённого файла внутри пакета
    converted_from: str = ""   # .doc -> .docx через LibreOffice

    @property
    def text(self) -> str:
        return "\n".join(u.text for u in self.units)


# ------------------------------------------------------------------ контекст чтения
class ReadContext:
    """Ограничения, счётчики и обратные вызовы (сохранить картинку страницы, прогресс)."""

    def __init__(self, limits: dict | None = None, preview: Callable | None = None,
                 progress: Callable | None = None, ocr: bool = True):
        self.limits = {"max_files": 500, "max_total_mb": 500, "max_zip_depth": 4, "max_pages": 300,
                       "max_ocr_pages": 150, "max_sheet_rows": 20000, "ocr_dpi": 300, "preview_dpi": 80,
                       "libreoffice_timeout_sec": 120, **(limits or {})}
        self.preview = preview           # preview(part_no, page_no, PIL.Image) -> относительный путь
        self.progress = progress         # progress(path) — начата очередная часть
        self.ocr = ocr
        self.parts = 0
        self.total = 0
        self.ocr_pages = 0
        self.warnings: list[str] = []

    def lim(self, key: str) -> int:
        return int(self.limits.get(key) or 0)


# ------------------------------------------------------------------ определение формата
def detect_format(name: str, data: bytes) -> str:
    ext = os.path.splitext(name.lower())[1]
    head = data[:16]
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        try:
            names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
        except zipfile.BadZipFile:
            return EXT_FORMAT.get(ext, "unknown")
        if "word/document.xml" in names:
            return "docx"
        if "xl/workbook.xml" in names:
            return "excel"
        if "content.xml" in names and ext in (".odt", ".ods"):
            return "legacy"
        return "zip"
    if head.startswith(b"\xd0\xcf\x11\xe0"):            # OLE: старые .xls / .doc
        if ext == ".xls":
            return "excel"
        if ext == ".msg":
            return "unknown"
        return "legacy"
    if head.startswith((b"\x89PNG", b"\xff\xd8\xff", b"II*\x00", b"MM\x00*", b"BM", b"GIF8")) or \
            (head[:4] == b"RIFF" and data[8:12] == b"WEBP"):
        return "image"
    if ext in EXT_FORMAT:
        return EXT_FORMAT[ext]
    stripped = data[:400].lstrip(b"\xef\xbb\xbf \r\n\t")
    if stripped.startswith(b"<?xml"):
        return "xml"
    if re.match(rb"(?im)^(received|from|mime-version|return-path|message-id|subject):", stripped):
        return "eml"
    return "unknown"


# ------------------------------------------------------------------ OCR
@functools.lru_cache(maxsize=1)
def _ocr_langs() -> str:
    return "+".join(ocr_reader.available_languages())


def ocr_available() -> bool:
    return ocr_reader.tesseract_available()


def ocr_image(img) -> tuple[list, float]:
    """Картинка -> (строки с уверенностью Tesseract, средняя уверенность 0…1)."""
    import pytesseract
    from PIL import ImageOps
    ocr_reader._use_tesseract()
    # Tesseract с OpenMP при нескольких параллельных распознаваниях «крутит» потоки впустую
    # и замедляется в десятки раз; для пакетной обработки — один поток на страницу.
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")
    gray = ImageOps.autocontrast(ImageOps.grayscale(img), cutoff=1)
    if max(gray.size) > 4000:
        ratio = 4000 / max(gray.size)
        gray = gray.resize((int(gray.width * ratio), int(gray.height * ratio)))
    td = ocr_reader._tessdata_dir()
    cfg = (f'--tessdata-dir "{td}" ' if td else "") + "--psm 6"
    data = pytesseract.image_to_data(gray, lang=_ocr_langs(), config=cfg, output_type=pytesseract.Output.DICT)
    groups: dict[tuple, list] = {}
    order: list[tuple] = []
    for i, word in enumerate(data.get("text") or []):
        word = (word or "").strip()
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if not word or conf < 0:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append((word, conf))
    lines, confs = [], []
    for key in order:
        words = groups[key]
        c = sum(cf for _, cf in words) / len(words) / 100.0
        lines.append(Line(" ".join(w for w, _ in words), round(c, 3)))
        confs.extend(cf for _, cf in words)
    mean = round(sum(confs) / len(confs) / 100.0, 3) if confs else 0.0
    return lines, mean


def _ocr_unit(img, unit: Unit, ctx: ReadContext) -> None:
    if not ctx.ocr:
        unit.method = "none"
        unit.warnings.append("распознавание (OCR) выключено")
        return
    if not ocr_available():
        unit.method = "none"
        unit.warnings.append("Tesseract не установлен — текст картинки не распознан "
                             "(Mac: bash install_ocr_mac.command, остальные — README.md)")
        return
    if ctx.ocr_pages >= ctx.lim("max_ocr_pages"):
        unit.method = "none"
        unit.warnings.append(f"превышен предел OCR на пакет ({ctx.lim('max_ocr_pages')} стр.)")
        return
    ctx.ocr_pages += 1
    unit.lines, unit.ocr_conf = ocr_image(img)
    unit.method = "ocr"


def _preview(ctx: ReadContext, part_no: int, page_no: int, img) -> str:
    if ctx.preview is None or not ctx.lim("preview_dpi"):
        return ""
    try:
        return ctx.preview(part_no, page_no, img) or ""
    except Exception:  # noqa: BLE001 — картинка для просмотра необязательна
        return ""


# ------------------------------------------------------------------ читатели форматов
def _read_pdf(part: Part, ctx: ReadContext) -> None:
    import pymupdf
    from PIL import Image
    try:
        doc = pymupdf.open(stream=part.data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise FormatError(f"PDF не открывается: {exc}")
    if doc.needs_pass and not doc.authenticate(""):
        raise FormatError("PDF защищён паролем")
    try:
        for i, page in enumerate(doc, start=1):
            if i > ctx.lim("max_pages"):
                part.message = f"прочитаны первые {ctx.lim('max_pages')} страниц из {doc.page_count}"
                break
            unit = Unit(kind="page", index=i, label=f"стр. {i}")
            text = page.get_text("text", sort=True) or ""
            if len("".join(text.split())) >= MIN_TEXT_LAYER_CHARS:
                unit.lines = [Line(t.strip(), 1.0) for t in text.splitlines() if t.strip()]
                unit.method = "text"
                try:
                    for tab in page.find_tables().tables:
                        grid = [[re.sub(r"\s+", " ", str(c or "")).strip() for c in row] for row in tab.extract()]
                        if len(grid) >= 2:
                            unit.tables.append(grid)
                except Exception:  # noqa: BLE001 — таблицы необязательны, текст уже есть
                    pass
            else:
                pix = page.get_pixmap(dpi=ctx.lim("ocr_dpi") or 300)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                _ocr_unit(img, unit, ctx)
            if ctx.lim("preview_dpi"):
                pv = page.get_pixmap(dpi=ctx.lim("preview_dpi"))
                unit.image = _preview(ctx, part.no, i, Image.open(io.BytesIO(pv.tobytes("png"))))
            part.units.append(unit)
    finally:
        doc.close()


def _read_image(part: Part, ctx: ReadContext) -> None:
    from PIL import Image, ImageSequence
    try:
        img = Image.open(io.BytesIO(part.data))
        frames = [f.copy() for f in ImageSequence.Iterator(img)]
    except Exception as exc:  # noqa: BLE001
        raise FormatError(f"картинка не открывается: {exc}")
    for i, frame in enumerate(frames or [img], start=1):
        if i > ctx.lim("max_pages"):
            part.message = f"прочитаны первые {ctx.lim('max_pages')} страниц"
            break
        if frame.mode not in ("RGB", "L"):
            frame = frame.convert("RGB")
        unit = Unit(kind="page", index=i, label=f"стр. {i}")
        _ocr_unit(frame, unit, ctx)
        unit.image = _preview(ctx, part.no, i, frame)
        part.units.append(unit)


def cell_str(v) -> str:
    """Значение ячейки -> строка (целые без «.0», даты ГГГГ-ММ-ДД)."""
    import math
    if v is None:
        return ""
    if isinstance(v, float):
        if math.isnan(v):
            return ""
        return str(int(v)) if v.is_integer() and abs(v) < 1e15 else repr(v)
    if hasattr(v, "strftime"):
        try:
            s = v.strftime("%Y-%m-%d %H:%M:%S")
            return s[:10] if s.endswith("00:00:00") else s
        except ValueError:
            return str(v)
    s = str(v).strip()
    return "" if s.lower() == "nan" else s


def _grid_lines(grid: list) -> list:
    return [Line("\t".join(c for c in row if c), 1.0) for row in grid if any(row)]


def _read_excel(part: Part, ctx: ReadContext) -> None:
    import pandas as pd
    engine = "xlrd" if part.data[:4] == b"\xd0\xcf\x11\xe0" else "openpyxl"
    try:
        book = pd.read_excel(io.BytesIO(part.data), sheet_name=None, header=None, dtype=object, engine=engine)
    except ImportError as exc:
        raise FormatError(f"для этого Excel нужен пакет: {exc}")
    except Exception as exc:  # noqa: BLE001
        raise FormatError(f"Excel не читается: {exc}")
    for i, (name, df) in enumerate(book.items(), start=1):
        max_rows = ctx.lim("max_sheet_rows")
        unit = Unit(kind="sheet", index=i, label=f"лист «{name}»", sheet=str(name), method="table")
        if len(df) > max_rows:
            unit.warnings.append(f"лист длиннее {max_rows} строк — прочитаны первые {max_rows}")
            df = df.head(max_rows)
        grid = [[cell_str(v) for v in row] for row in df.itertuples(index=False, name=None)]
        while grid and not any(grid[-1]):
            grid.pop()
        if not grid:
            continue
        unit.tables = [grid]
        unit.lines = _grid_lines(grid)
        part.units.append(unit)


def sniff_grid(text: str) -> list:
    sample = text[:20000]
    try:
        delim = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        first = sample.split("\n", 1)[0]
        counts = {d: first.count(d) for d in (",", ";", "\t", "|")}
        delim = max(counts, key=counts.get) if max(counts.values()) else ","
    return [[c.strip() for c in row] for row in csv.reader(io.StringIO(text), delimiter=delim)]


def _read_csv(part: Part, ctx: ReadContext) -> None:
    text, enc = _decode_text(part.data)
    grid = sniff_grid(text)[: ctx.lim("max_sheet_rows")]
    unit = Unit(kind="sheet", index=1, label="таблица CSV", method="table", tables=[grid] if grid else [],
                lines=_grid_lines(grid))
    unit.warnings = [] if enc.startswith("utf-8") else [f"кодировка {enc}"]
    part.units.append(unit)


def _xml_safe(data: bytes) -> bytes:
    head = data[:5000].decode("utf-8", "ignore")
    if re.search(r"<!DOCTYPE[^>]*\[|<!ENTITY", head, re.I):
        raise FormatError("XML с объявлениями сущностей (DOCTYPE/ENTITY) не принимается")
    return data


def _docx_text(el) -> str:
    out = []
    for node in el.iter():
        tag = node.tag
        if tag == W_NS + "t" and node.text:
            out.append(node.text)
        elif tag == W_NS + "tab":
            out.append("\t")
        elif tag in (W_NS + "br", W_NS + "cr"):
            out.append("\n")
    return "".join(out)


def _read_docx(part: Part, ctx: ReadContext) -> None:
    try:
        zf = zipfile.ZipFile(io.BytesIO(part.data))
        body_xml = _xml_safe(zf.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError) as exc:
        raise FormatError(f"документ Word повреждён: {exc}")
    root = ET.fromstring(body_xml)
    lines: list = []
    tables: list = []
    for name in sorted(n for n in zf.namelist() if re.match(r"word/header\d*\.xml$", n)):
        try:
            lines += [Line(t.strip()) for t in _docx_text(ET.fromstring(_xml_safe(zf.read(name)))).splitlines()
                      if t.strip()]
        except ET.ParseError:
            pass
    body = root.find(W_NS + "body")
    for el in (list(body) if body is not None else []):
        if el.tag == W_NS + "p":
            lines += [Line(t.strip()) for t in _docx_text(el).splitlines() if t.strip()]
        elif el.tag == W_NS + "tbl":
            grid = []
            for tr in el.iter(W_NS + "tr"):
                row = [re.sub(r"\s+", " ", " ".join(_docx_text(p) for p in tc.iter(W_NS + "p"))).strip()
                       for tc in tr.findall(W_NS + "tc")]
                grid.append(row)
            if grid:
                tables.append(grid)
                lines += _grid_lines(grid)
    part.units.append(Unit(kind="text", index=1, label="документ", lines=lines, tables=tables, method="text"))


def libreoffice_cmd() -> str | None:
    """Путь к LibreOffice (soffice), если он установлен."""
    env = os.environ.get("SIBDOC_SOFFICE")
    if env and os.path.isfile(env):
        return env
    for p in (shutil.which("soffice"), shutil.which("libreoffice"),
              "/Applications/LibreOffice.app/Contents/MacOS/soffice",
              r"C:\Program Files\LibreOffice\program\soffice.exe",
              r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"):
        if p and os.path.isfile(p):
            return p
    return None


def convert_legacy(data: bytes, name: str, timeout: int = 120) -> tuple[bytes, str]:
    """.doc/.rtf/.odt/.ods -> (байты .docx/.xlsx, новое имя) через LibreOffice без сети."""
    ext = os.path.splitext(name.lower())[1]
    target = LEGACY_TARGET.get(ext, "docx")
    cmd = libreoffice_cmd()
    if not cmd:
        raise FormatError(f"Для формата {ext} нужен LibreOffice (не найден). Сохраните файл как "
                          f".{target} или установите LibreOffice — остальные документы пакета обработаны.")
    tmp = tempfile.mkdtemp(prefix="sibdoc-lo-")
    try:
        src = os.path.join(tmp, "input" + ext)
        with open(src, "wb") as fh:
            fh.write(data)
        profile = "file://" + os.path.join(tmp, "profile").replace("\\", "/")
        proc = subprocess.run([cmd, f"-env:UserInstallation={profile}", "--headless", "--norestore",
                               "--convert-to", target, "--outdir", tmp, src],
                              capture_output=True, timeout=timeout)
        out = os.path.join(tmp, "input." + target)
        if not os.path.exists(out):
            raise FormatError(f"LibreOffice не смог преобразовать {ext}: "
                              f"{proc.stderr.decode('utf-8', 'replace')[-300:] or 'без сообщения'}")
        with open(out, "rb") as fh:
            return fh.read(), os.path.splitext(name)[0] + "." + target
    except subprocess.TimeoutExpired:
        raise FormatError(f"LibreOffice не успел преобразовать файл за {timeout} с")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _xml_lines(root) -> list:
    out = []
    for el in root.iter():
        txt = (el.text or "").strip()
        if txt:
            out.append(Line(f"{xml_decl._local(el.tag)}: {re.sub(chr(10) + '+', ' ', txt)}"))
    return out


def _read_xml(part: Part, ctx: ReadContext) -> None:
    try:
        root = xml_decl.parse_root(part.data)
    except xml_decl.XmlDeclError as exc:
        raise FormatError(str(exc))
    unit = Unit(kind="xml", index=1, label="XML", method="xml", xml_root=xml_decl._local(root.tag),
                xml_profile=xml_decl.detect_profile(root) or "")
    unit.lines = _xml_lines(root)
    part.units.append(unit)


def _html_to_text(s: str) -> str:
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</h\d>|</li>", "\n", s)
    s = re.sub(r"(?i)</td>|</th>", "\t", s)
    return html.unescape(re.sub(r"<[^>]+>", " ", s))


def _text_unit(text: str, label: str = "текст") -> Unit:
    return Unit(kind="text", index=1, label=label, method="text",
                lines=[Line(re.sub(r"[ \t]+", " ", t).strip()) for t in text.splitlines() if t.strip()])


def _read_txt(part: Part, ctx: ReadContext) -> None:
    text, _ = _decode_text(part.data)
    part.units.append(_text_unit(_html_to_text(text) if part.fmt == "html" else text))


# ------------------------------------------------------------------ контейнеры: ZIP и письма
def _zip_name(info: zipfile.ZipInfo) -> str:
    """Имена в ZIP из Windows часто в cp866, а флаг UTF-8 не выставлен."""
    name = info.filename
    if info.flag_bits & 0x800:
        return name
    try:
        raw = name.encode("cp437")
    except UnicodeEncodeError:
        return name
    for enc in ("utf-8", "cp866", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return name


def _read_zip(data: bytes, path: str, ctx: ReadContext, depth: int, out: list) -> None:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        out.append(_bad(ctx, path, "zip", "архив ZIP повреждён"))
        return
    members = [m for m in zf.infolist() if not m.is_dir()]
    if not members:
        out.append(_bad(ctx, path, "zip", "архив пустой", status="unsupported"))
        return
    for m in members:
        name = _zip_name(m)
        base = os.path.basename(name.rstrip("/"))
        if not base or base.startswith(("~$", ".")) or "__MACOSX/" in name or base in ("Thumbs.db", "desktop.ini"):
            continue
        inner = f"{path}/{name}"
        if m.flag_bits & 0x1:
            out.append(_bad(ctx, inner, detect_format(base, b""), "файл в архиве защищён паролем"))
            continue
        if ctx.total + m.file_size > ctx.lim("max_total_mb") * 1024 * 1024:
            out.append(_bad(ctx, inner, "unknown", f"превышен объём пакета ({ctx.lim('max_total_mb')} МБ)"))
            continue
        try:
            content = zf.read(m)
        except Exception as exc:  # noqa: BLE001
            out.append(_bad(ctx, inner, "unknown", f"не удалось распаковать: {exc}"))
            continue
        read_any(content, inner, ctx, depth=depth + 1, out=out, container=path)


def _read_eml(data: bytes, path: str, ctx: ReadContext, depth: int, out: list, container: str) -> None:
    try:
        msg = email.message_from_bytes(data, policy=email.policy.default)
    except Exception as exc:  # noqa: BLE001
        out.append(_bad(ctx, path, "eml", f"письмо не читается: {exc}"))
        return
    head = []
    for key, title in (("From", "От"), ("To", "Кому"), ("Date", "Дата"), ("Subject", "Тема")):
        if msg.get(key):
            head.append(f"{title}: {msg.get(key)}")
    body, attachments = "", []
    for p in msg.walk():
        if p.is_multipart():
            continue
        fname = p.get_filename()
        ctype = p.get_content_type()
        disp = (p.get_content_disposition() or "").lower()
        if fname or disp == "attachment" or ctype == "message/rfc822":
            payload = p.get_payload(decode=True)
            if payload is None and ctype == "message/rfc822":
                inner = p.get_payload()
                payload = inner[0].as_bytes() if isinstance(inner, list) and inner else b""
            attachments.append((fname or ("письмо.eml" if ctype == "message/rfc822" else "вложение.bin"),
                                payload or b""))
            continue
        if ctype == "text/plain" and not body:
            try:
                body = p.get_content()
            except Exception:  # noqa: BLE001
                body = (p.get_payload(decode=True) or b"").decode("utf-8", "replace")
        elif ctype == "text/html" and not body:
            try:
                body = _html_to_text(p.get_content())
            except Exception:  # noqa: BLE001
                body = ""
    part = _new_part(ctx, path, "eml", data, container)
    unit = _text_unit("\n".join(head) + "\n" + (body or ""), label="письмо")
    if attachments:
        unit.lines.append(Line("Вложения: " + ", ".join(n for n, _ in attachments)))
    part.units.append(unit)
    out.append(part)
    for fname, payload in attachments:
        read_any(payload, f"{path}/{fname}", ctx, depth=depth + 1, out=out, container=path)


# ------------------------------------------------------------------ общий вход
def _new_part(ctx: ReadContext, path: str, fmt: str, data: bytes, container: str = "") -> Part:
    ctx.parts += 1
    return Part(no=ctx.parts, path=path, name=os.path.basename(path), fmt=fmt, data=data, container=container)


def _bad(ctx: ReadContext, path: str, fmt: str, message: str, status: str = "error") -> Part:
    p = _new_part(ctx, path, fmt, b"")
    p.status, p.message = status, message
    return p


READERS = {"pdf": _read_pdf, "image": _read_image, "excel": _read_excel, "csv": _read_csv, "docx": _read_docx,
           "xml": _read_xml, "txt": _read_txt, "html": _read_txt}


def read_any(data: bytes, path: str, ctx: ReadContext, depth: int = 0, out: list | None = None,
             container: str = "") -> list:
    """Файл (байты + путь в пакете) -> список частей-листьев. Ошибки не выбрасываются."""
    out = [] if out is None else out
    name = os.path.basename(path)
    if ctx.parts >= ctx.lim("max_files"):
        if not ctx.warnings or "предел" not in ctx.warnings[-1]:
            ctx.warnings.append(f"достигнут предел файлов в пакете ({ctx.lim('max_files')}) — остальные пропущены")
        return out
    ctx.total += len(data)
    fmt = detect_format(name, data)
    if fmt == "zip":
        if depth >= ctx.lim("max_zip_depth"):
            out.append(_bad(ctx, path, "zip", f"слишком глубокая вложенность архивов (> {ctx.lim('max_zip_depth')})"))
        else:
            _read_zip(data, path, ctx, depth, out)
        return out
    if fmt == "eml":
        _read_eml(data, path, ctx, depth, out, container)
        return out
    part = _new_part(ctx, path, fmt, data, container)
    if ctx.progress:
        try:
            ctx.progress(path)
        except Exception:  # noqa: BLE001
            pass
    try:
        if fmt == "legacy":
            new_data, new_name = convert_legacy(data, name, ctx.lim("libreoffice_timeout_sec") or 120)
            part.converted_from = os.path.splitext(name)[1].lower()
            part.fmt = detect_format(new_name, new_data)
            part.data = new_data
            fmt = part.fmt
        reader = READERS.get(fmt)
        if reader is None:
            ext = os.path.splitext(name.lower())[1]
            part.status = "unsupported"
            part.message = UNSUPPORTED_HINTS.get(ext) or f"формат «{ext or 'без расширения'}» не поддерживается"
        else:
            reader(part, ctx)
            if not part.units or not any(u.lines or u.tables for u in part.units):
                if any(u.method == "none" for u in part.units):
                    part.message = part.message or "; ".join(w for u in part.units for w in u.warnings)
                elif not part.message:
                    part.message = "в файле не найден текст"
    except FormatError as exc:
        part.status, part.message = ("unsupported" if part.fmt == "legacy" else "error"), str(exc)
    except Exception as exc:  # noqa: BLE001 — один файл не должен ронять пакет
        part.status, part.message = "error", f"ошибка чтения: {exc}"
    out.append(part)
    return out


def read_batch(files: list, ctx: ReadContext | None = None) -> list:
    """[(байты, имя)] -> части-листья всех файлов пакета."""
    ctx = ctx or ReadContext()
    out: list = []
    for data, name in files:
        read_any(data, name, ctx, out=out)
    return out
