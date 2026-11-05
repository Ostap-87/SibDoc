from __future__ import annotations
# -*- coding: utf-8 -*-
"""ВЭД-пакет: чтение документов отгрузки — коммерческий инвойс, упаковочный лист,
транспортный документ (CMR, коносамент B/L, накладная СМГС, авианакладная AWB),
сертификаты и декларации соответствия ЕАЭС, сертификаты происхождения.

Источники:
  - Excel / CSV: одна строка = одна товарная позиция (или контейнер, или документ
    в реестре). Шапка документа (номер, дата, валюта, условия поставки…) бывает
    в двух видах — оба понимаются:
      а) повторяется в колонках каждой строки;
      б) блоком над таблицей: «Invoice No: INV-1», «Дата | 10.09.2026» и т. п.
    Строка «Итого / Total / 合计» становится итогами документа (…_total).
  - PDF с текстовым слоем и сканы — через ocr_reader (тот же путь, что у сканов
    деклараций); поля ищутся паттернами config/scan_fields.yaml → package.

Колонки переименовываются в поля единого словаря (config/fields.yaml); отдельный
документ узнаётся по номеру (invoice_no / packing_no / bill_of_lading / cert_no).
"""
import csv
import io
import os
import re

import numpy as np
import pandas as pd
import yaml

from app import fields as fdict
from app.normalizers import is_blank, norm_number, norm_text

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCAN_FIELDS_PATH = os.path.join(PROJECT_ROOT, "config", "scan_fields.yaml")

KINDS = ("invoice", "packing", "transport", "conformity")
SCAN_EXTS = (".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp")
TABLE_EXTS = (".xlsx", ".xlsm", ".xls", ".csv", ".txt")

# номер документа каждого вида
NUMBER_FIELD = {"invoice": "invoice_no", "packing": "packing_no", "transport": "bill_of_lading",
                "conformity": "cert_no"}
DATE_FIELD = {"invoice": "invoice_date", "packing": "packing_date", "transport": "transport_doc_date",
              "conformity": "issue_date"}
# поля, по которым узнаётся «свой» лист / таблица документа
KIND_HINTS = {
    "invoice": {"invoice_no", "invoice_date", "unit_price", "total_value", "invoice_total", "seller", "buyer"},
    "packing": {"packing_no", "packages", "gross_weight", "net_weight", "seal_no", "package_type"},
    "transport": {"bill_of_lading", "transport_doc_date", "seal_no", "packages", "gross_weight", "doc_type"},
    "conformity": {"cert_no", "issue_date", "expiry_date", "issuer", "origin_country", "doc_type"},
}
# количественные поля строки -> поле итога документа (для блока шапки и строки «Итого»)
TOTAL_OF = {"net_weight": "net_weight_total", "gross_weight": "gross_weight_total", "packages": "packages_total",
            "total_value": "invoice_total", "qty": "qty_total"}
WEIGHT_FIELDS = ("net_weight", "gross_weight", "net_weight_total", "gross_weight_total")
_TONNES = re.compile(r"(?i)(\(\s*t\s*\)|,\s*т\b|\bт\.?\s*$|\(т\)|тонн|\btons?\b|\btonnes?\b|\bmt\b|吨)")
_TOTAL_ROW = re.compile(r"(?i)^\s*(итого|всего|total|grand\s+total|sum|合计|总计|小计)\b")

# подписи видов документов и типов (RU, ZH) — для проверки, отчётов и интерфейса
KIND_LABELS = {
    "invoice": ("инвойс", "商业发票"),
    "packing": ("упаковочный лист", "装箱单"),
    "transport": ("транспортный документ", "运输单据"),
    "ru_dt": ("ДТ РФ", "俄罗斯报关单"),
    "cn_dec": ("报关单 КНР", "中国报关单"),
    "conformity": ("сертификат", "证书"),
}
DOC_TYPE_LABELS = {
    "SMGS": ("накладная СМГС", "国际铁路联运运单"),
    "CMR": ("CMR", "CMR公路运单"),
    "AWB": ("авианакладная AWB", "航空运单"),
    "B/L": ("коносамент B/L", "提单"),
    "ST-1": ("сертификат происхождения СТ-1", "原产地证书 ST-1"),
    "FORM-A": ("сертификат происхождения Form A", "原产地证书 Form A"),
    "FORM-E": ("сертификат происхождения Form E", "原产地证书 Form E"),
    "CO": ("сертификат происхождения", "原产地证书"),
    "EAEU-DECL": ("декларация о соответствии ЕАЭС", "欧亚经济联盟符合性声明"),
    "EAEU-CERT": ("сертификат соответствия ЕАЭС", "欧亚经济联盟合格证书"),
}


class PackageReadError(ValueError):
    pass


# ------------------------------------------------------------------ настройки паттернов
def package_patterns() -> dict:
    """config/scan_fields.yaml → package (detect, doc_types, fields, multi)."""
    if not os.path.exists(SCAN_FIELDS_PATH):
        return {}
    with open(SCAN_FIELDS_PATH, encoding="utf-8") as fh:
        return (yaml.safe_load(fh) or {}).get("package") or {}


def detect_kind(text: str, head_chars: int = 600) -> str | None:
    """Вид документа по заголовку (первые строки текста): invoice | packing | transport | conformity."""
    head = (text or "")[:head_chars]
    for kind, pats in (package_patterns().get("detect") or {}).items():
        for p in pats or []:
            if re.search(p, head, re.I | re.U):
                return kind
    return None


def norm_doc_type(kind: str, value) -> str:
    """Свободная запись вида документа («Накладная СМГС», «коносамент», «СТ-1») -> SMGS, B/L, ST-1, …"""
    s = norm_text(value)
    if not s:
        return ""
    up = s.upper().replace(" ", "")
    for code in DOC_TYPE_LABELS:
        if up == code.replace(" ", ""):
            return code
    for code, pats in ((package_patterns().get("doc_types") or {}).get(kind) or {}).items():
        for p in pats or []:
            if re.search(p, s, re.I | re.U):
                return code
    return s.upper()


def doc_type_label(code: str, lang: str = "ru") -> str:
    lab = DOC_TYPE_LABELS.get(code)
    return (lab[1] if lang == "zh" else lab[0]) if lab else code


# ------------------------------------------------------------------ разбор текста (PDF / скан)
def parse_text_fields(text: str, kind: str) -> dict[str, dict]:
    """Текст документа -> {поле: {value, status, matched_by}} (как parse_declaration)."""
    pk = package_patterns()
    multi = set(pk.get("multi") or [])
    out: dict[str, dict] = {}
    for field, pats in ((pk.get("fields") or {}).get(kind) or {}).items():
        values, used = [], ""
        for p in pats or []:
            if field in multi:
                for m in re.finditer(p, text or "", re.I | re.U):
                    v = re.sub(r"\s+", "" if field != "hs_code" else "", m.group(1)).upper()
                    if v and v not in values:
                        values.append(v)
                        used = used or p
                if values:
                    break
            else:
                m = re.search(p, text or "", re.I | re.U)
                if m:
                    values, used = [re.sub(r"\s{2,}", " ", m.group(1)).strip(" .,;:")], p
                    break
        value = "; ".join(values)
        if field in ("invoice_total", "net_weight_total", "gross_weight_total", "packages_total"):
            num = norm_number(value)
            value = "" if np.isnan(num) else (f"{num:.3f}".rstrip("0").rstrip("."))
        status = "found" if value else "missing"
        if field == "container" and value and not all(re.fullmatch(r"[A-Z]{4}\d{7}", c) for c in value.split("; ")):
            status = "review"
        out[field] = {"value": value, "status": status, "matched_by": used}
    dt = norm_doc_type(kind, _first_type_hit(text, kind))
    if kind in ("transport", "conformity"):
        out["doc_type"] = {"value": dt, "status": "found" if dt else "missing", "matched_by": "doc_types"}
    return out


def _first_type_hit(text: str, kind: str) -> str:
    for code, pats in ((package_patterns().get("doc_types") or {}).get(kind) or {}).items():
        for p in pats or []:
            if re.search(p, (text or "")[:1500], re.I | re.U):
                return code
    return ""


def text_to_frame(text: str, kind: str, filename: str) -> pd.DataFrame:
    """Один документ из текста -> одна строка таблицы слота."""
    fields = parse_text_fields(text, kind)
    rec = {k: v["value"] for k, v in fields.items() if v.get("value")}
    rec["source_file"] = filename
    return pd.DataFrame([rec])


# ------------------------------------------------------------------ разбор таблицы (Excel / CSV)
def _cell_text(v) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ""
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    return str(v).strip()


def _field_of(label: str, idx: dict) -> str | None:
    """Поле словаря по подписи: целиком или по частям «Invoice No. / № инвойса / 发票号»."""
    s = _cell_text(label)
    if not s or len(s) > 80:
        return None
    f = idx.get(fdict.header_key(s))
    if f:
        return f
    for part in re.split(r"[/|\n]+", s):
        f = idx.get(fdict.header_key(part))
        if f:
            return f
    # «Брутто, т», «Net weight (MT)», «净重(吨)», «Amount, USD» — без единицы измерения
    for part in [s] + re.split(r"[/|\n]+", s):
        base = _UNIT_SUFFIX.sub("", part)
        if base != part:
            f = idx.get(fdict.header_key(base))
            if f:
                return f
    return None


_UNIT_SUFFIX = re.compile(r"[\s,]*[(（]?\s*(?:кг|kg|kgs|т|t|тн|mt|tons?|tonnes?|公斤|千克|吨|usd|cny|rub|eur|"
                          r"руб|шт|pcs)\.?\s*[)）]?\s*$", re.I)


def _is_value_like(v) -> bool:
    if isinstance(v, (int, float, np.integer, np.floating)) or hasattr(v, "strftime"):
        return True
    s = _cell_text(v)
    return bool(re.fullmatch(r"[\d\s.,:/\-]+", s))


def _header_row(grid: list[list], idx: dict, hints: set) -> tuple[int, int]:
    """-> (строка заголовков таблицы, оценка). Оценка: распознанные поля ×3 (+2 для «своих» полей
    документа) минус прочие непустые ячейки — так строка шапки «Invoice No: | INV-1» не побеждает таблицу."""
    best = (-1, 0)
    for r, row in enumerate(grid[:60]):
        seen, other = set(), 0
        for v in row:
            if _cell_text(v) == "":
                continue
            # «Invoice No:» с двоеточием — подпись шапки, а не заголовок колонки
            f = None if _is_value_like(v) or _cell_text(v).endswith((":", "：")) else _field_of(v, idx)
            if f and f not in seen:
                seen.add(f)
            else:
                other += 1
        if len(seen) < 2:
            continue
        sc = 3 * len(seen) + 2 * len(seen & hints) - other
        if sc > best[1]:
            best = (r, sc)
    return best if best[1] >= 6 else (-1, 0)


# общие подписи шапки, смысл которых зависит от вида документа («Дата», «Номер», «Итого»)
_GENERIC = {"date": "date", "дата": "date", "日期": "date", "датадокумента": "date", "dated": "date",
            "number": "number", "номер": "number", "no": "number", "编号": "number", "号码": "number",
            "total": "total", "итого": "total", "всего": "total", "合计": "total", "总计": "total"}
_NUM_DATE = re.compile(r"^(.*?)\s+(?:от|dd\.?|dated|date|of|日期)\s+(\d{1,4}[./\-年]\d{1,2}[./\-月]\d{1,4}日?)\s*$", re.I)


def _block_field(label, idx: dict, kind: str) -> str | None:
    f = _field_of(label, idx)
    if f == "item_no":                       # «No.:» в шапке — номер самого документа
        f = "number"
    if not f:
        txt = _cell_text(label)
        for part in [txt] + re.split(r"[/|\n]+", txt):
            f = _GENERIC.get(fdict.header_key(part))
            if f:
                break
    if f == "date":
        return DATE_FIELD.get(kind)
    if f == "number":
        return NUMBER_FIELD.get(kind)
    if f == "total":
        return "invoice_total" if kind == "invoice" else None
    return f


def _block_fields(rows: list[list], idx: dict, kind: str = "") -> dict[str, object]:
    """Блок шапки / подвала документа: «Подпись: значение» в ячейке или подпись и значение в соседних."""
    out: dict[str, object] = {}

    def put(f, val):
        if not f or is_blank(val):
            return
        if f == NUMBER_FIELD.get(kind) and isinstance(val, str):
            m = _NUM_DATE.match(val.strip())          # «INV-1 от 10.09.2026»
            if m:
                val = m.group(1).strip(" ,№")
                out.setdefault(DATE_FIELD[kind], m.group(2))
        out.setdefault(f, val)

    for row in rows:
        cells = [v for v in row if _cell_text(v) != ""]
        i = 0
        while i < len(cells):
            v = cells[i]
            txt = _cell_text(v)
            m = re.match(r"^(.{1,60}?)\s*[:：]\s*(.*)$", txt) if not _is_value_like(v) else None
            if m and _block_field(m.group(1), idx, kind):
                f = _block_field(m.group(1), idx, kind)
                val = m.group(2).strip()
                if not val and i + 1 < len(cells) and not _block_field(cells[i + 1], idx, kind):
                    val, i = cells[i + 1], i + 1
                put(f, val)
            elif not _is_value_like(v) and _block_field(txt, idx, kind) and i + 1 < len(cells) \
                    and not _block_field(cells[i + 1], idx, kind):
                put(_block_field(txt, idx, kind), cells[i + 1])
                i += 1
            i += 1
    return out


def _title_doc_type(rows: list[list], kind: str) -> str:
    text = "\n".join(_cell_text(v) for row in rows for v in row if _cell_text(v))
    hit = _first_type_hit(text, kind)
    return norm_doc_type(kind, hit) if hit else ""


def grid_to_frame(grid: list[list], kind: str, slot_cfg: dict, filename: str) -> tuple[pd.DataFrame, dict]:
    """Таблица ячеек (лист Excel / CSV) -> строки документа(ов) с полями словаря."""
    idx = fdict.synonyms_index()
    aliases = dict(slot_cfg.get("aliases") or {})
    hints = KIND_HINTS.get(kind, set())
    grid = [list(r) for r in grid]
    h, score = _header_row(grid, idx, hints)
    info: dict = {"doc_kind": kind}
    totals: dict[str, object] = {}
    rows_out: list[dict] = []
    tail: list[list] = []
    if h >= 0:
        header = [_cell_text(v) for v in grid[h]]
        cols, used = [], set()
        for i, name in enumerate(header):
            f = _field_of(name, idx) if name else None
            f = aliases.get(f, f)
            if f and f not in used:
                used.add(f)
                cols.append((i, f, name))
            elif name:
                cols.append((i, name if name not in used else f"{name}_{i}", name))
                used.add(cols[-1][1])
        r = h + 1
        while r < len(grid):
            row = grid[r]
            texts = [_cell_text(v) for v in row]
            if not any(texts):
                if rows_out:                      # пустая строка после данных — конец таблицы
                    break
                r += 1
                continue
            if any(_TOTAL_ROW.match(t) for t in texts[:4] if t):
                for i, f, _ in cols:
                    if f in TOTAL_OF and i < len(row) and not is_blank(row[i]):
                        totals[TOTAL_OF[f]] = row[i]
                r += 1
                break
            rows_out.append({f: (row[i] if i < len(row) else None) for i, f, _ in cols})
            r += 1
        tail = grid[r:]
        info.update({"header_row": h + 1, "mapped": [{"field": f, "from": n, "to": f} for _, f, n in cols
                                                        if f != n and f in fdict.dictionary()]})
        units = {f: n for _, f, n in cols}
    else:
        units = {}
    block_rows = (grid[:h] if h >= 0 else grid) + tail
    block = _block_fields(block_rows, idx, kind)
    block = {aliases.get(f, f): v for f, v in block.items()}
    # количественные поля в шапке — это итоги документа
    block = {TOTAL_OF.get(f, f): v for f, v in block.items()}
    if kind in ("transport", "conformity") and "doc_type" not in block:
        dt = _title_doc_type(grid[:max(h, 0) + 3] if h >= 0 else grid[:6], kind)
        if dt:
            block["doc_type"] = dt
    df = pd.DataFrame(rows_out) if rows_out else pd.DataFrame([{}])
    for f, v in {**totals, **block}.items():
        if f not in df.columns:
            df[f] = v
        else:
            df[f] = [v if is_blank(x) else x for x in df[f]]
    # единицы веса: «Нетто, т», «Gross (MT)», «净重(吨)» -> кг
    unit_cfg = dict(slot_cfg.get("units") or {})            # sources.<слот>.units: {net_weight: t} — весь слот в тоннах
    for f in WEIGHT_FIELDS:
        src_name = units.get(f, "")
        in_t = bool(src_name and _TONNES.search(src_name)) or unit_cfg.get(f.replace("_total", "")) == "t"
        if f in df.columns and in_t:
            df[f] = df[f].map(lambda v: norm_number(v) * 1000 if not np.isnan(norm_number(v)) else v)
            info.setdefault("converted_t_to_kg", []).append(f)
    if "doc_type" in df.columns:
        df["doc_type"] = df["doc_type"].map(lambda v: norm_doc_type(kind, v) if not is_blank(v) else v)
    df = df.dropna(how="all").reset_index(drop=True)
    df = df.loc[:, [c for c in df.columns if not df[c].map(is_blank).all()]]
    if "source_file" in df.columns:          # SibDoc3: строки из пакета уже знают свой документ
        src = df.pop("source_file").map(lambda v: filename if is_blank(v) else v)
        df.insert(0, "source_file", src)
    else:
        df.insert(0, "source_file", filename)
    info.update({"header_block": sorted(block), "totals": sorted(totals), "table": h >= 0, "score": score})
    return df, info


def _excel_grids(data: bytes, filename: str) -> list[tuple[str, list[list]]]:
    engine = "xlrd" if filename.lower().endswith(".xls") else "openpyxl"
    try:
        book = pd.ExcelFile(io.BytesIO(data), engine=engine)
    except ImportError as exc:
        raise PackageReadError(f"для {os.path.splitext(filename)[1]} нужен пакет: {exc}")
    out = []
    for sh in book.sheet_names:
        raw = book.parse(sh, header=None, dtype=object)
        out.append((str(sh), raw.astype(object).where(pd.notna(raw), None).values.tolist()))
    return out


def _csv_grid(data: bytes) -> tuple[list[list], dict]:
    from app.services.loader import _decode_text          # одна логика кодировок с загрузчиком
    text, enc = _decode_text(data)
    sample = text[:20000]
    try:
        delim = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        counts = {d: sample.count(d) for d in (",", ";", "\t", "|")}
        delim = max(counts, key=counts.get) if max(counts.values()) else ","
    grid = [row for row in csv.reader(io.StringIO(text), delimiter=delim)]
    return grid, {"encoding": enc, "delimiter": {"\t": "TAB"}.get(delim, delim)}


def _sheet_score(name: str, grid: list[list], kind: str) -> int:
    idx = fdict.synonyms_index()
    _, sc = _header_row(grid, idx, KIND_HINTS.get(kind, set()))
    block = _block_fields(grid[:25], idx, kind)
    sc += 2 * len(set(block) & KIND_HINTS.get(kind, set()))
    for p in (package_patterns().get("detect") or {}).get(kind) or []:
        if re.search(p, name, re.I | re.U):
            sc += 5
            break
    return sc


def read_document(data: bytes, filename: str, slot_cfg: dict) -> tuple[pd.DataFrame, dict]:
    """Файл документа пакета -> (таблица, сведения о чтении)."""
    kind = slot_cfg.get("package")
    low = filename.lower()
    if low.endswith(SCAN_EXTS):
        from app.services.ocr_reader import read_scan
        try:
            res = read_scan(io.BytesIO(data), filename)
        except RuntimeError as exc:                       # нет tesseract для скана
            raise PackageReadError(str(exc))
        df = text_to_frame(res["text"], kind, filename)
        fmt = "pdf (текстовый слой)" if res.get("text_layer_pages") == res.get("pages") else "OCR"
        found = [c for c in df.columns if c != "source_file"]
        return df, {"format": fmt, "pages": res.get("pages"), "doc_kind": kind, "text_fields": found,
                    "mapped": [], "detected_kind": detect_kind(res["text"])}
    if low.endswith((".csv", ".txt")):
        grid, meta = _csv_grid(data)
        df, info = grid_to_frame(grid, kind, slot_cfg, filename)
        return df, {"format": "csv", **meta, **info}
    if low.endswith((".xlsx", ".xlsm", ".xls")):
        grids = _excel_grids(data, filename)
        if not grids:
            raise PackageReadError("в файле нет листов с данными")
        pinned = slot_cfg.get("sheet")
        if pinned not in (None, ""):
            grids = [g for g in grids if g[0] == str(pinned)] or grids[:0]
            if not grids:
                raise PackageReadError(f"лист «{pinned}» не найден в файле")
        name, grid = max(grids, key=lambda g: _sheet_score(g[0], g[1], kind))
        df, info = grid_to_frame(grid, kind, slot_cfg, filename)
        return df, {"format": "excel", "sheet": name, "sheets": [g[0] for g in grids], **info}
    raise PackageReadError(f"Неподдерживаемый формат для документа пакета: {filename} "
                           f"(можно: {', '.join(TABLE_EXTS + SCAN_EXTS)}, .zip)")
