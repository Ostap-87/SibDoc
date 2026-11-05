from __future__ import annotations
# -*- coding: utf-8 -*-
"""Пакетная загрузка, шаг 3: поля и товарные строки документа в единую таблицу.

Каждое поле называется именем единого словаря (config/fields.yaml) и хранит,
откуда взято: файл, страница или лист, строка / колонка, способ (xml, таблица,
текст, OCR, шаблон) и уверенность 0…1.

Реестр экстракторов (подключаемые разборщики по типу документа):

    from app.intake.extract import register_extractor, generic

    def parse_invoice(ctx):            # ctx: DocContext — тип, единицы (страницы/листы), байты файла
        res = generic(ctx)             # общий разбор «подпись: значение» + таблицы по словарю
        ...                            # дополнить своими полями
        return res                     # None — «не мой документ», передать следующему

    register_extractor("invoice", parse_invoice, name="инвойс", priority=50)

Экстракторы типа вызываются по возрастанию priority; первый, вернувший не None,
побеждает; общий разбор (generic) — всегда последний. Встроенные: ДТ РФ и 报关单
(XML по профилям config/xml_profiles.yaml, сканы — словарь + шаблоны
config/scan_fields.yaml). Инвойсы, упаковочные листы, транспортные документы,
сертификаты, выписки банка подключатся своими разборщиками; до тех пор у них
общий разбор по словарю полей и исходный текст.
"""
import functools
import math
import os
import re
from dataclasses import dataclass, field
from typing import Callable

import yaml

from app import fields as fdict
from app.intake.fieldmap import field_for_label, find_header
from app.normalizers import (INCOTERMS, container_check_digit_ok, is_blank, latinize, norm_date,
                             norm_decl, norm_hs, norm_incoterms, norm_number, norm_text)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "intake", "extract.yaml")
DECL_TYPES = ("ru_dt", "cn_dec")
SCAN_FIELD_MAP = {"decl_num": "decl_cn", "container_num": "container", "declared_model": "model"}
_NUM_TOKEN = re.compile(r"[-+]?\d[\d  '’]*(?:[.,]\d+)*")
_CONTAINER_RX = re.compile(r"(?<![A-Z])([A-Z]{4})\s?([0-9OIlSZB]{7})(?![0-9A-Z])")
_OCR_DIGITS = str.maketrans("OIlSZB", "011528")


def find_containers(text: str) -> tuple[list, list]:
    """Номера контейнеров в тексте -> (номера, исправленные OCR). «DEMUO002019» (O вместо 0) исправляется,
    только если после замены сходится контрольная цифра ISO 6346."""
    out, fixed = [], []
    for owner, digits in _CONTAINER_RX.findall(latinize(text)):
        raw = owner + digits
        if digits.isdigit():
            c = raw
        else:
            c = owner + digits.translate(_OCR_DIGITS)
            if not container_check_digit_ok(c):
                continue
            fixed.append(f"{raw} → {c}")
        if c not in out:
            out.append(c)
    return out, fixed
_RU_DT_RX = re.compile(r"(?<!\d)(\d{8}\s*/\s*\d{6}\s*/\s*[A-ZА-Я]?\d{7})(?!\d)")
_CJK_SPACE = re.compile(r"(?<=[\u2e80-\u9fff\uff00-\uffef])\s+(?=[\u2e80-\u9fff\uff00-\uffef])")
_KV_SPLIT = re.compile(r"^(?P<label>[^:：\t]{1,70}?)\s*[:：]\s*(?P<value>.+)$")
_CJK_PREFIX = re.compile(r"^(?P<label>[一-鿿][一-鿿\s()（）]{1,14})\s*(?P<value>[^一-鿿\s].*)$")


@functools.lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def config() -> dict:
    return _load(CONFIG_PATH, os.path.getmtime(CONFIG_PATH)) if os.path.exists(CONFIG_PATH) else {}


def item_fields() -> set:
    return set(config().get("item_fields") or [])


# ------------------------------------------------------------------ контекст и реестр
@dataclass
class DocContext:
    doc_id: str
    type: str
    units: list                       # list[formats.Unit]
    file: str                         # путь файла в пакете
    fmt: str
    data: bytes = b""                 # байты файла (для XML)
    warnings: list = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(u.text for u in self.units)


_REGISTRY: dict[str, list[tuple[int, str, Callable]]] = {}


def register_extractor(doc_type: str, fn: Callable, name: str | None = None, priority: int = 100) -> None:
    """Подключить разборщик документов типа doc_type (см. описание модуля)."""
    lst = _REGISTRY.setdefault(doc_type, [])
    lst[:] = [x for x in lst if x[2] is not fn]
    lst.append((int(priority), name or getattr(fn, "__name__", "extractor"), fn))
    lst.sort(key=lambda x: x[0])


def extractors(doc_type: str | None = None) -> dict:
    """Какие разборщики подключены (для API /api/intake/info)."""
    items = _REGISTRY.items() if doc_type is None else [(doc_type, _REGISTRY.get(doc_type, []))]
    return {t: [n for _, n, _ in lst] + ["общий разбор"] for t, lst in items}


# ------------------------------------------------------------------ значения
def _field_type(f: str) -> str:
    return (fdict.dictionary().get(f) or {}).get("type", "text")


def normalize(f: str, raw) -> tuple[object, bool, str]:
    """-> (значение для сравнения, прошло ли проверку формата, пояснение)."""
    if raw is None or (isinstance(raw, float) and math.isnan(raw)):
        return None, False, "пусто"
    t = _field_type(f) if f in fdict.dictionary() else ""
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and t in ("number", ""):
        return float(raw), True, ""
    s = _CJK_SPACE.sub("", norm_text(raw)).strip(" ;:；：,，|")
    if not s:
        return None, False, "пусто"
    if f == "container":
        conts, fixed = find_containers(s)
        if not conts:
            return None, False, "не похоже на номер контейнера"
        bad = [c for c in conts if not container_check_digit_ok(c)]
        note = "; ".join(x for x in [f"контрольная цифра не сходится: {', '.join(bad)}" if bad else "",
                                     f"исправлено OCR: {', '.join(fixed)}" if fixed else ""] if x)
        return "; ".join(conts), not bad, note
    if f in ("contract", "main_contract", "add_contract"):
        v = re.sub(r"^(?:№|no\.?|n°|#)\s*", "", s, flags=re.I).strip()
        v = re.split(r"\s+/\s+|\s{2,}|\s+(?:от|from|dated|签订)\s+", v, maxsplit=1)[0].strip(" ,;")
        return v, bool(re.search(r"\d", v)), ""
    if f == "item_no":
        m = re.match(r"\s*№?\s*(\d{1,4})(?!\d)", s)
        return (m.group(1), True, "") if m else (None, False, "не номер строки")
    if f in ("decl_ru",):
        v = norm_decl(s)
        return v, bool(re.fullmatch(r"\d{8}/\d{6}/[A-Z]?\d{7}", v)), ""
    if f == "decl_cn":
        m = re.search(r"\d{18}", re.sub(r"[\s\-]", "", s))
        return (m.group(0) if m else re.sub(r"\D", "", s)), bool(m), ""
    if f == "hs_code":
        m = re.search(r"\d[\d \t.]{3,14}\d", s)
        v = norm_hs(m.group(0)) if m else norm_hs(s)
        return v, 6 <= len(v) <= 10, "" if 6 <= len(v) <= 10 else "код короче 6 или длиннее 10 цифр"
    if f in ("incoterms", "sap_incoterms"):
        term, place = norm_incoterms(s)
        return term, term in INCOTERMS or term in ("C&I",), (f"место: {place}" if place else "")
    if t == "currency":
        from app.checks._common import norm_currency_safe
        cur = norm_currency_safe(re.sub(r"[\d\s.,]+", " ", s).strip().split(" ")[0] if s else s)
        return cur, bool(re.fullmatch(r"[A-Z]{3}", cur or "")), ""
    if t == "number":
        m = _NUM_TOKEN.search(s)
        v = norm_number(m.group(0)) if m else float("nan")
        return (None, False, "не число") if math.isnan(v) else (v, True, "")
    if t == "date":
        d = norm_date(s)
        return (d or s), bool(d), "" if d else "дата не распознана"
    if f == "model":
        s = re.sub(r"(?<=[A-Za-z])!(?=[A-Za-z\s]|$)", "l", s).rstrip("!").strip()   # «l», прочитанная OCR как «!»
    return s[:500], True, ""


def make_value(f: str, raw, conf: float, source: dict, how: str = "") -> dict:
    value, valid, note = normalize(f, raw)
    c = conf * (1.0 if valid else 0.6)
    return {"value": value, "raw": norm_text(raw) if not isinstance(raw, (int, float)) else raw,
            "conf": round(max(0.0, min(1.0, c)), 3), "valid": bool(valid), "note": note,
            "source": source, "how": how}


# ------------------------------------------------------------------ сбор значений
class Collector:
    """Поля шапки и товарных строк; при повторе поля остаётся значение с большей уверенностью."""

    def __init__(self, doc_type: str):
        self.type = doc_type
        rm = config().get("remap") or {}
        self.remap = (rm[doc_type] if doc_type in rm else rm.get("default")) or {}
        self.header: dict[str, dict] = {}
        self.items: dict[str, dict] = {}         # ключ -> {"no": …, "fields": {…}, "source": {…}}
        self.kv: list[dict] = []

    def field(self, f: str) -> str:
        return self.remap.get(f, f)

    def put(self, target: dict, f: str, fv: dict) -> None:
        if fv["value"] in (None, ""):
            return
        cur = target.get(f)
        if f == "container" and cur:
            merged = [c for c in (cur["value"] or "").split("; ") if c]
            for c in str(fv["value"]).split("; "):
                if c and c not in merged:
                    merged.append(c)
            cur = dict(cur, value="; ".join(merged), conf=min(cur["conf"], fv["conf"]),
                       valid=cur["valid"] and fv["valid"])
            target[f] = cur
        elif cur is None or fv["conf"] > cur["conf"]:
            target[f] = fv

    def add(self, f: str, raw, conf: float, source: dict, how: str = "", item: str | None = None,
            item_no: str | None = None) -> None:
        f = self.field(f)
        fv = make_value(f, raw, conf, source, how)
        if item is None:
            self.put(self.header, f, fv)
        else:
            rec = self.items.setdefault(item, {"no": item_no or str(len(self.items) + 1), "fields": {},
                                               "source": {k: v for k, v in source.items() if k != "col"}})
            self.put(rec["fields"], f, fv)


def _src(ctx: DocContext, unit, method: str, **kw) -> dict:
    s = {"file": ctx.file, "method": method}
    if unit is not None:
        if unit.kind == "page":
            s["page"] = unit.index
        if unit.sheet:
            s["sheet"] = unit.sheet
    s.update({k: v for k, v in kw.items() if v is not None})
    return s


def _line_method(unit) -> str:
    return "ocr" if unit.method == "ocr" else ("xml" if unit.kind == "xml" else
                                               ("table" if unit.kind == "sheet" else "text"))


def _base_conf(unit, line_conf: float) -> float:
    if unit.method == "ocr":
        return 0.95 * line_conf
    return 1.0 if unit.kind in ("sheet", "xml") else 0.95


# ------------------------------------------------------------------ «подпись: значение»
def _pairs_from_cells(cells: list) -> list[tuple[str, str, int]]:
    """Ячейки строки -> пары (подпись, значение, колонка): подпись — ячейка-поле словаря, значение — следующая."""
    out = []
    i = 0
    while i < len(cells) - 1:
        lab, val = cells[i].strip(), cells[i + 1].strip()
        f, _ = field_for_label(lab) if lab else (None, "")
        if f and val and not field_for_label(val)[0]:
            out.append((lab, val, i))
            i += 2
            continue
        m = _KV_SPLIT.match(lab) if lab else None
        if m and field_for_label(m.group("label"))[0]:
            out.append((m.group("label"), m.group("value"), i))
        i += 1
    if cells:
        m = _KV_SPLIT.match(cells[-1].strip())
        if m and field_for_label(m.group("label"))[0] and (len(cells) == 1 or not out or out[-1][2] < len(cells) - 2):
            out.append((m.group("label"), m.group("value"), len(cells) - 1))
    return out


def split_kv(text: str) -> tuple[str, str] | None:
    """Строка текста -> (подпись, значение) или None."""
    t = text.strip()
    m = _KV_SPLIT.match(t)
    if m and not m.group("label").strip().isdigit():
        return m.group("label"), m.group("value")
    m = _CJK_PREFIX.match(t)
    if m and field_for_label(m.group("label"))[0]:
        return m.group("label"), m.group("value")
    toks = t.split()
    for k in range(min(5, len(toks) - 1), 0, -1):
        lab = " ".join(toks[:k])
        f, how = field_for_label(lab)
        if f and how == "exact":
            rest = toks[k:]
            # строка заголовков таблицы («Контейнер Описание товара Мест …») — не пара «подпись значение»
            if any(field_for_label(" ".join(rest[:j]))[1] == "exact" for j in range(1, min(4, len(rest)) + 1)):
                return None
            return lab, " ".join(rest)
    return None


def kv_from_unit(ctx: DocContext, unit, col: Collector, markers: list, items_ok: bool = True) -> int:
    """Строки текста единицы -> поля (с учётом блоков товаров). -> сколько блоков товаров найдено."""
    cur_item, blocks = None, 0
    ifields = item_fields()
    for ln_no, ln in enumerate(unit.lines, start=1):
        text = ln.text
        if items_ok:
            for rx in markers:
                m = rx.match(text)
                if m:
                    cur_item = f"{unit.index}:{m.group(1)}"
                    col.items.setdefault(cur_item, {"no": m.group(1), "fields": {},
                                                    "source": _src(ctx, unit, _line_method(unit), line=ln_no)})
                    blocks += 1
                    text = text[m.end():].strip(" .:：-")
                    break
        pairs = []
        if "\t" in text:
            pairs = [(a, b) for a, b, _ in _pairs_from_cells(text.split("\t"))]
        else:
            kv = split_kv(text)
            pairs = [kv] if kv else []
        for label, value in pairs:
            f, how = field_for_label(label)
            if not f:
                if len(col.kv) < 200 and len(label) <= 60:
                    col.kv.append({"label": label.strip(), "value": value.strip()[:300],
                                   **{k: v for k, v in _src(ctx, unit, _line_method(unit)).items() if k != "file"}})
                continue
            f = col.field(f)
            in_item = cur_item is not None and (f in ifields or f == "container")
            col.add(f, value, _base_conf(unit, ln.conf),
                    _src(ctx, unit, _line_method(unit), line=ln_no, ocr_conf=ln.conf if unit.method == "ocr" else None),
                    how=f"подпись «{label.strip()}»" + ("" if how == "exact" else " (частично)"),
                    item=cur_item if in_item else None)
    return blocks


# ------------------------------------------------------------------ таблицы
def _col_letter(i: int) -> str:
    s, i = "", i + 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def table_items(ctx: DocContext, unit, grid: list, tab_no: int, col: Collector) -> int:
    """Таблица -> товарные строки по заголовку из словаря; строки над заголовком — пары «подпись — значение»."""
    hdr, mapping = find_header(grid)
    if hdr is None:
        return 0
    totals = [str(x).lower() for x in config().get("totals_markers") or []]
    for r in range(hdr):
        for label, value, c in _pairs_from_cells([str(x) for x in grid[r]]):
            f, how = field_for_label(label)
            if f:
                col.add(f, value, _base_conf(unit, 1.0), _src(ctx, unit, _line_method(unit), row=r + 1,
                                                                col=_col_letter(c)), how=f"подпись «{label}»")
    n = 0
    for r in range(hdr + 1, len(grid)):
        row = [str(x) for x in grid[r]]
        if not any(x.strip() for x in row):
            if n:
                break
            continue
        first = " ".join(x for x in row[:3] if x).lower()
        is_total = any(re.search(rf"(?<![a-zа-я]){re.escape(t)}", first) for t in totals)
        vals = {c: row[c] for c in mapping if c < len(row) and row[c].strip()}
        if not vals:
            continue
        if is_total:
            for c, v in vals.items():
                f = col.field(mapping[c])
                if f in ("total_value", "net_weight", "gross_weight", "qty"):
                    col.add(f, v, 0.9, _src(ctx, unit, "table", row=r + 1, col=_col_letter(c)), how="строка «итого»")
            continue
        key = f"t{unit.index}.{tab_no}.{r}"
        item_no = vals.get(next((c for c, f in mapping.items() if f == "item_no"), -1)) or str(len(col.items) + 1)
        for c, v in vals.items():
            col.add(mapping[c], v, _base_conf(unit, 1.0) if unit.method != "ocr" else 0.8,
                    _src(ctx, unit, "table", row=r + 1, col=_col_letter(c)),
                    how=f"колонка «{grid[hdr][c]}»", item=key, item_no=str(item_no))
        n += 1
    return n


# ------------------------------------------------------------------ шаблоны и запасные правила
def _find_line(unit, needle: str):
    nd = re.sub(r"\s", "", str(needle))
    for i, ln in enumerate(unit.lines, start=1):
        if nd and nd in re.sub(r"\s", "", ln.text):
            return i, ln
    return None, None


def scan_patterns(ctx: DocContext, col: Collector, item_level: bool) -> None:
    """Шаблоны сканов деклараций (config/scan_fields.yaml) — для полей, не найденных по подписям."""
    from app.services.decl_parser import parse_declaration
    ifields = item_fields()
    for unit in ctx.units:
        if not unit.lines or unit.kind in ("sheet", "xml"):
            continue
        parsed = parse_declaration(unit.text)
        for name, rec in parsed.items():
            f = col.field(SCAN_FIELD_MAP.get(name, name))
            if rec.get("status") == "missing" or not rec.get("value"):
                continue
            if f in ifields and not item_level:
                continue
            if f in col.header and f != "container":
                continue
            ln_no, ln = _find_line(unit, rec["value"])
            conf = (ln.conf if ln is not None and unit.method == "ocr" else (unit.ocr_conf or 1.0)
                    if unit.method == "ocr" else 1.0)
            col.add(f, rec["value"], 0.85 * conf * (1.0 if rec.get("status") == "found" else 0.7),
                    _src(ctx, unit, "pattern", line=ln_no, ocr_conf=conf if unit.method == "ocr" else None),
                    how="шаблон config/scan_fields.yaml")


def fallback_patterns(ctx: DocContext, col: Collector, decl_ru: bool = True) -> None:
    """Контейнеры (ISO 6346) и номер ДТ РФ по всему тексту — где бы они ни стояли."""
    for unit in ctx.units:
        for ln_no, ln in enumerate(unit.lines, start=1):
            conts, _ = find_containers(ln.text)
            if conts:
                col.add("container", "; ".join(conts), _base_conf(unit, ln.conf) * 0.95,
                        _src(ctx, unit, "pattern", line=ln_no), how="номер контейнера в тексте")
            if decl_ru and "decl_ru" not in col.header:
                m = _RU_DT_RX.search(ln.text)
                if m:
                    col.add("decl_ru", m.group(1), _base_conf(unit, ln.conf) * 0.9,
                            _src(ctx, unit, "pattern", line=ln_no), how="номер ДТ в тексте")


# ------------------------------------------------------------------ итог
def _aggregate(col: Collector) -> None:
    """Поля, одинаковые во всех товарных строках, — в шапку; контейнеры строк — в общий список."""
    if not col.items:
        return
    ifields = item_fields()
    allf = {f for it in col.items.values() for f in it["fields"]}
    for f in allf - ifields:
        vals = [it["fields"].get(f) for it in col.items.values()]
        present = [v for v in vals if v and v["value"] not in (None, "")]
        if f == "container":
            for v in present:
                col.put(col.header, "container", v)
            continue
        if f in col.header or not present:
            continue
        if len({str(v["value"]) for v in present}) == 1 and len(present) == len(vals):
            col.header[f] = dict(present[0], how=(present[0].get("how") or "") + " (общее для всех строк)")


def _derived(col: Collector) -> None:
    """Место поставки из условий («CPT ЗАБАЙКАЛЬСК» -> ЗАБАЙКАЛЬСК), если отдельно не указано."""
    inc = col.header.get("incoterms")
    if inc and "delivery_place" not in col.header and str(inc.get("note", "")).startswith("место: "):
        col.header["delivery_place"] = dict(inc, value=inc["note"][len("место: "):], note="", valid=True,
                                            how=(inc.get("how") or "") + " (место из условий поставки)")


def _single_item(col: Collector) -> None:
    ifields = item_fields()
    moved = {f: v for f, v in col.header.items() if f in ifields}
    if not moved:
        return
    for f in moved:
        col.header.pop(f)
    fields_ = dict(moved)
    if "container" in col.header:
        fields_["container"] = col.header["container"]
    first = next(iter(moved.values()))
    col.items["1"] = {"no": str((moved.get("item_no") or {}).get("value") or "1"), "fields": fields_,
                      "source": {k: v for k, v in (first.get("source") or {}).items() if k in ("file", "page", "sheet")}}


def _json_safe(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def finalize(ctx: DocContext, col: Collector, extractor: str) -> dict:
    cfg = config()
    _aggregate(col)
    _derived(col)
    for fv in list(col.header.values()) + [v for it in col.items.values() for v in it["fields"].values()]:
        fv["value"] = _json_safe(fv["value"])
    warnings = list(ctx.warnings)
    labels = {f: fdict.label(f) for f in cfg.get("key_fields", {}).get(ctx.type, [])}
    for f, lab in labels.items():
        have = f in col.header or any(f in it["fields"] for it in col.items.values())
        if not have:
            warnings.append(f"не найдено поле «{lab}»")
    items = list(col.items.values())
    for i, it in enumerate(items, start=1):
        it.setdefault("no", str(i))
    return {"fields": col.header, "items": items, "kv": col.kv, "warnings": warnings, "extractor": extractor}


def _markers() -> list:
    return [re.compile(x, re.I) for x in config().get("item_markers") or []]


def generic(ctx: DocContext, declaration: bool = False) -> dict:
    """Общий разбор: таблицы по словарю полей + строки «подпись: значение» + запасные шаблоны."""
    col = Collector(ctx.type)
    markers = _markers()
    table_rows = 0
    for unit in ctx.units:
        for t_no, grid in enumerate(unit.tables or [], start=1):
            table_rows += table_items(ctx, unit, grid, t_no, col)
    blocks = 0
    for unit in ctx.units:
        if unit.kind == "sheet" and table_rows:
            # строки листа с таблицей уже разобраны; берём только пары над/под таблицей
            kv_from_unit(ctx, unit, col, markers, items_ok=False)
        else:
            blocks += kv_from_unit(ctx, unit, col, markers, items_ok=not table_rows)
    if declaration:
        scan_patterns(ctx, col, item_level=not (table_rows or blocks))
    fallback_patterns(ctx, col, decl_ru=not table_rows)
    if declaration and not col.items:
        _single_item(col)
    return finalize(ctx, col, "общий разбор" if not declaration else "декларация (словарь + шаблоны)")


# ------------------------------------------------------------------ декларации: XML и сканы
def declaration(ctx: DocContext) -> dict | None:
    """ДТ РФ / 报关单: XML по профилям xml_profiles.yaml; остальное — общий разбор + шаблоны сканов."""
    unit = ctx.units[0] if ctx.units else None
    if unit is not None and unit.kind == "xml" and unit.xml_profile and ctx.data:
        from app.services import xml_decl
        df, meta = xml_decl.parse_xml(ctx.data, ctx.file)
        col = Collector(ctx.type)
        ifields = item_fields()
        for c in df.columns:
            if c in ifields or c == "container":
                continue
            vals = [v for v in df[c].tolist() if not is_blank(v)]
            if vals and len({str(v) for v in vals}) == 1:
                col.add(c, vals[0], 1.0, _src(ctx, unit, "xml", profile=meta.get("profile")), how="XML")
        for i, (_, r) in enumerate(df.iterrows(), start=1):
            key = f"x{i}"
            for c in df.columns:
                if (c in ifields or c == "container") and not is_blank(r.get(c)):
                    col.add(c, r.get(c), 1.0, _src(ctx, unit, "xml", row=i), how="XML",
                            item=key, item_no=str(r.get("item_no") or i))
        return finalize(ctx, col, f"XML: {meta.get('title')}")
    return generic(ctx, declaration=True)


register_extractor("ru_dt", declaration, name="ДТ РФ: XML ФТС / скан", priority=50)
register_extractor("cn_dec", declaration, name="报关单: XML / скан", priority=50)


def extract(ctx: DocContext) -> dict:
    """Разобрать документ подходящим экстрактором (по типу), общий разбор — запасной."""
    chain = list(_REGISTRY.get(ctx.type, [])) + [(999, "общий разбор", generic)]
    errors = []
    for _, name, fn in chain:
        try:
            res = fn(ctx)
        except Exception as exc:  # noqa: BLE001 — сбой подключённого разборщика не роняет пакет
            errors.append(f"{name}: {exc}")
            continue
        if res is not None:
            res.setdefault("extractor", name)
            res.setdefault("warnings", [])
            res["warnings"] = [f"разборщик {e}" for e in errors] + res["warnings"]
            return res
    return {"fields": {}, "items": [], "kv": [], "warnings": [f"разборщик {e}" for e in errors],
            "extractor": "нет"}
