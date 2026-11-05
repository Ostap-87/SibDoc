from __future__ import annotations
# -*- coding: utf-8 -*-
"""1С: формат обмена EnterpriseData (Коннекторы/банк) -> таблицы слотов SibDoc.

Файл EnterpriseData (Message / msg:Header / Body) содержит объекты «Документ.<Вид>» и
«Справочник.<Вид>». Какие объекты нужны и в какой слот они идут — config/enterprise_data.yaml:
  Документ.ПоступлениеТоваровУслуг -> purchase (строка = строка товаров),
  Документ.ГТДИмпорт               -> ru_dt (строка = товар ГТД),
  Справочник.Договоры               -> contract_register (договоры с контрагентами),
  Справочник.Контрагенты            -> справочник для подстановки ссылок.
Один файл даёт данные сразу нескольким слотам: parse() возвращает {слот: таблица};
загрузчик берёт часть для своего слота.
"""
import os
import re
import xml.etree.ElementTree as ET

import pandas as pd
import yaml

from app.normalizers import norm_date, norm_number, norm_text, norm_unit

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PROFILE_PATH = os.path.join(PROJECT_ROOT, "config", "enterprise_data.yaml")
NS_HINT = "EnterpriseData"


class EnterpriseDataError(ValueError):
    pass


def load_profile() -> dict:
    if not os.path.exists(PROFILE_PATH):
        return {}
    with open(PROFILE_PATH, encoding="utf-8") as fh:
        return (yaml.safe_load(fh) or {}).get("objects") or {}


def _local(tag) -> str:
    return str(tag).rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def is_enterprise_data(data: bytes) -> bool:
    head = data[:3000]
    try:
        text = head.decode("utf-8", "ignore")
    except Exception:  # noqa: BLE001
        return False
    return ("<Message" in text or ":Message" in text) and (NS_HINT in text or "Справочник." in text
                                                           or "Документ." in text)


# ------------------------------------------------------------------ поиск значений по путям
def _step(nodes: list, name: str, deep: bool) -> list:
    out = []
    for n in nodes:
        it = n.iter() if deep else list(n)
        for ch in it:
            if ch is n:
                continue
            if _local(ch.tag) == name:
                out.append(ch)
    return out


def _resolve(el, path: str) -> list:
    """'Контрагент//Наименование' -> элементы; '/' — дочерний, '//' — на любой глубине."""
    parts = re.split(r"(//|/)", path)
    nodes, deep = [el], False
    for p in parts:
        if p == "//":
            deep = True
            continue
        if p == "/" or p == "":
            continue
        nodes = _step(nodes, p, deep)
        deep = False
        if not nodes:
            return []
    return nodes


def _text(el) -> str:
    """Текст элемента; у ссылки без текста — пусто (её значение — во вложенных свойствах)."""
    if el is None or len(el):
        return ""
    return (el.text or "").strip()


class _Doc:
    def __init__(self, root):
        self.root = root
        self.refs: dict[str, ET.Element] = {}

    def index(self, obj: ET.Element) -> None:
        for r in _resolve(obj, "КлючевыеСвойства/Ссылка") + _resolve(obj, "Ссылка")[:1]:
            if _text(r):
                self.refs.setdefault(_text(r), obj)

    def value(self, el, specs) -> str:
        specs = specs if isinstance(specs, list) else [specs]
        for spec in specs:
            for node in _resolve(el, spec):
                v = _text(node)
                if v:
                    return v
            # ссылка выгружена только идентификатором -> значение из справочника того же файла
            if "//" in spec:
                head, tail = spec.split("//", 1)
                for holder in _resolve(el, head):
                    ref = next((_text(r) for r in holder.iter() if _local(r.tag) == "Ссылка" and _text(r)), "")
                    obj = self.refs.get(ref)
                    if obj is None or obj is el:
                        continue
                    for node in _resolve(obj, "//" + tail):
                        v = _text(node)
                        if v:
                            return v
        return ""


def _decode(data: bytes) -> str:
    from app.services.xml_decl import _decode as dec      # кодировка + запрет DOCTYPE/ENTITY
    return dec(data)


def parse(data: bytes, filename: str = "") -> tuple[dict[str, pd.DataFrame], dict]:
    """-> ({слот: таблица}, сведения: версия формата, объекты по видам)."""
    try:
        root = ET.fromstring(_decode(data))
    except ET.ParseError as exc:
        raise EnterpriseDataError(f"XML EnterpriseData не читается: {exc}")
    profile = load_profile()
    body = next((e for e in root.iter() if _local(e.tag) == "Body"), None)
    if body is None:
        raise EnterpriseDataError("в файле EnterpriseData нет раздела Body")
    fmt = next((norm_text(e.text) for e in root.iter() if _local(e.tag) == "Format" and e.text), "")
    if not fmt:
        m = re.search(r"EnterpriseData/([\d.]+)", body.tag)
        fmt = m.group(0) if m else ""
    doc = _Doc(root)
    counts: dict[str, int] = {}
    for obj in body:
        kind = _local(obj.tag)
        counts[kind] = counts.get(kind, 0) + 1
        if (profile.get(kind) or {}).get("ref_index") or kind.startswith("Справочник."):
            doc.index(obj)
    tables: dict[str, list[dict]] = {}
    for obj in body:
        kind = _local(obj.tag)
        prof = profile.get(kind)
        if not prof or not prof.get("slot"):
            continue
        hdr = {f: doc.value(obj, spec) for f, spec in (prof.get("header") or {}).items()}
        hdr["source_object"] = kind
        lines = []
        for lp in prof.get("lines") or []:
            lines = _resolve(obj, lp)
            if lines:
                break
        rows = tables.setdefault(prof["slot"], [])
        if not lines:
            rows.append(dict(hdr))
            continue
        for i, ln in enumerate(lines, start=1):
            rec = dict(hdr)
            rec.update({f: doc.value(ln, spec) for f, spec in (prof.get("line_fields") or {}).items()})
            rec.setdefault("item_no", str(i))
            rows.append(rec)
    out = {slot: _post(slot, pd.DataFrame(rows)) for slot, rows in tables.items() if rows}
    meta = {"format": "1С EnterpriseData", "version": fmt, "objects": counts,
            "slots": {s: int(len(df)) for s, df in out.items()}, "file": filename}
    return out, meta


def _contract_no(v: str) -> str:
    """«Контракт № ДЕМО.1001 от 10.01.2026» -> «ДЕМО.1001» (если номер не выгружен отдельно)."""
    s = norm_text(v)
    m = re.search(r"(?:№|\bNo\.?|\bN)\s*([^\s,;]*\d[^\s,;]*)", s) or \
        re.search(r"(?i)(?:контракт|договор)\w*\s+(\S*\d\S*)", s)
    return m.group(1).rstrip(".,;") if m else s


def _post(slot: str, df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    from app.services.bank_statements import _norm_ccy
    for col in ("Currency", "currency"):
        if col in df.columns:
            df[col] = df[col].map(lambda v: _norm_ccy(v) or v)
    for col in ("Shipment date", "decl_date", "contract_date", "Дата договора"):
        if col in df.columns:
            df[col] = df[col].map(lambda v: norm_date(v) or v)
    for col in ("Main contract", "contract"):
        if col in df.columns:
            df[col] = df[col].map(_contract_no)
    if slot == "purchase" and "Количество" in df.columns:
        # единица кг/т -> вес нетто в тоннах и цена за тонну (так их ждёт слот purchase)
        unit = df.get("Единица", pd.Series([""] * len(df))).map(lambda u: norm_unit(u) or norm_text(u).lower())
        qty = df["Количество"].map(norm_number)
        price = df.get("Цена 1С", pd.Series([None] * len(df))).map(norm_number)
        k = unit.map({"kg": 0.001, "t": 1.0})
        df["Net weight"] = (qty * k).where(k.notna())
        df["Price"] = (price / k).where(k.notna())
    for col in ("customs_value", "total_value", "contract_amount", "Сумма", "Сумма документа", "qty"):
        if col in df.columns:
            df[col] = df[col].map(lambda v: norm_number(v) if norm_text(v) else None)
    return df.loc[:, [c for c in df.columns if not df[c].map(lambda v: norm_text(v) == "").all()]]
