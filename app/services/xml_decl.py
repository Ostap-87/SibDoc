from __future__ import annotations
# -*- coding: utf-8 -*-
"""Чтение электронных деклараций в XML: ДТ РФ (ФТС, ESADout_CU) и 报关单 КНР (DecMessage).

Правила разбора — в config/xml_profiles.yaml (теги на каждое поле, коды валют,
где искать контейнеры и контракт). Результат — таблица «одна строка = один
товар декларации», колонки — поля единого словаря (config/fields.yaml) плюс
всё из шапки, чтобы декларацию можно было показать полностью.
"""
import io
import os
import re
import xml.etree.ElementTree as ET

import pandas as pd
import yaml

from app.normalizers import CN_TRANS_MODE, norm_date, norm_hs, norm_number, norm_text, norm_unit

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PROFILES_PATH = os.path.join(PROJECT_ROOT, "config", "xml_profiles.yaml")

NUMERIC_FIELDS = {"net_weight", "gross_weight", "total_value", "customs_value", "statistical_value",
                  "qty", "unit_price", "first_qty", "currency_rate", "net_weight_total",
                  "gross_weight_total", "freight", "insurance", "extra_costs"}
_RU_DT_RX = re.compile(r"\b(\d{8})/(\d{6})/(\d{7})\b")
MAX_XML_BYTES = 50 * 1024 * 1024


class XmlDeclError(ValueError):
    pass


def load_profiles() -> dict:
    if not os.path.exists(PROFILES_PATH):
        return {}
    with open(PROFILES_PATH, encoding="utf-8") as fh:
        return (yaml.safe_load(fh) or {}).get("profiles") or {}


def _local(tag) -> str:
    return str(tag).rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _decode(content: bytes) -> str:
    """Байты XML -> текст с учётом объявленной кодировки (windows-1251 expat сам не понимает)."""
    if len(content) > MAX_XML_BYTES:
        raise XmlDeclError("XML больше 50 МБ — проверьте файл")
    if content.startswith(b"\xef\xbb\xbf"):
        content = content[3:]
    head = content[:200].decode("ascii", "ignore")
    m = re.search(r'encoding=["\']([\w\-]+)["\']', head)
    enc = (m.group(1) if m else "utf-8").lower()
    for e in (enc, "utf-8", "cp1251", "gb18030"):
        try:
            text = content.decode(e)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        raise XmlDeclError("не удалось определить кодировку XML")
    if re.search(r"<!DOCTYPE[^>]*\[|<!ENTITY", text[:5000], re.I):
        raise XmlDeclError("XML с объявлениями сущностей (DOCTYPE/ENTITY) не принимается")
    return re.sub(r"^\s*<\?xml[^>]*\?>", "", text, count=1)


def parse_root(content: bytes) -> ET.Element:
    try:
        return ET.fromstring(_decode(content))
    except ET.ParseError as exc:
        raise XmlDeclError(f"XML не читается: {exc}")


def detect_profile(root: ET.Element, profiles: dict | None = None) -> str | None:
    profiles = profiles if profiles is not None else load_profiles()
    names = {_local(root.tag)}
    for i, el in enumerate(root.iter()):
        names.add(_local(el.tag))
        if i > 3000:
            break
    for pid, prof in profiles.items():
        if names & set(prof.get("detect") or []):
            return pid
    return None


# ------------------------------------------------------------------ поиск по дереву
def _iter_skip(el: ET.Element, skip: set):
    """Обход без заходов внутрь элементов из skip (товарные блоки)."""
    for ch in el:
        if ch in skip:
            continue
        yield ch
        yield from _iter_skip(ch, skip)


def _find(scope, names, skip: set | None = None) -> str:
    names = names if isinstance(names, list) else [names]
    it = list(_iter_skip(scope, skip)) if skip else list(scope.iter())
    for name in names:
        for el in it:
            if _local(el.tag) == name and (el.text or "").strip():
                return el.text.strip()
    return ""


def _find_all(scope, names, skip: set | None = None) -> list[str]:
    names = names if isinstance(names, list) else [names]
    it = list(_iter_skip(scope, skip)) if skip else list(scope.iter())
    out = []
    for el in it:
        if _local(el.tag) in names and (el.text or "").strip():
            v = el.text.strip()
            if v not in out:
                out.append(v)
    return out


def _find_el(scope, names, skip: set | None = None):
    names = names if isinstance(names, list) else [names]
    it = list(_iter_skip(scope, skip)) if skip else list(scope.iter())
    for name in names:
        for el in it:
            if _local(el.tag) == name:
                return el
    return None


def _path(scope, path: list[str], skip: set | None = None) -> str:
    el = _find_el(scope, path[0], skip)
    for name in path[1:]:
        if el is None:
            return ""
        el = _find_el(el, name)
    return (el.text or "").strip() if el is not None and el.text else ""


def _value(scope, spec, skip=None) -> str:
    if isinstance(spec, dict):
        if "path" in spec:
            return _path(scope, spec["path"], skip)
        vals = _find_all(scope, spec.get("tags") or [], skip)
        return (spec.get("join") or " ").join(vals)
    return _find(scope, spec, skip)


def _ru_date_short(d: str) -> str:
    """'2026-01-02' | '02.01.2026' -> '020126'."""
    d = d.strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", d)
    if m:
        return f"{m.group(3)}{m.group(2)}{m.group(1)[2:]}"
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", d)
    if m:
        return f"{m.group(1)}{m.group(2)}{m.group(3)[2:]}"
    return re.sub(r"\D", "", d)[:6]


def _decl_ru(root, hdr_spec: dict, skip: set, text: str) -> str:
    parts = hdr_spec.get("decl_ru_parts") or {}
    for within in parts.get("within") or []:
        blk = _find_el(root, within, skip)
        if blk is None:
            continue
        c, d, n = _find(blk, parts.get("customs") or []), _find(blk, parts.get("date") or []), \
            _find(blk, parts.get("number") or [])
        if c and d and n:
            return f"{c}/{_ru_date_short(d)}/{n}"
    direct = _find(root, hdr_spec.get("decl_ru") or [], skip)
    if direct:
        return direct
    m = _RU_DT_RX.search(text)
    return m.group(0) if m else ""


def _map(v: str, cmap: dict | None) -> str:
    if not v or not cmap:
        return v
    return str(cmap.get(str(v).strip(), v))


# ------------------------------------------------------------------ основной разбор
def parse_xml(content: bytes, filename: str = "", profile: str | None = None) -> tuple[pd.DataFrame, dict]:
    """XML декларации -> (таблица по товарам, сведения о разборе)."""
    profiles = load_profiles()
    root = parse_root(content)
    pid = profile or detect_profile(root, profiles)
    if not pid or pid not in profiles:
        raise XmlDeclError("Неизвестный формат XML: не похоже ни на ДТ ФТС, ни на 报关单. "
                           "Добавьте профиль в config/xml_profiles.yaml")
    prof = profiles[pid]
    text = ET.tostring(root, encoding="unicode")
    item_tags = (prof.get("items") or {}).get("tag") or []
    items = [el for el in root.iter() if _local(el.tag) in item_tags]
    skip = set(items)
    cmaps = prof.get("code_maps") or {}

    hdr_spec = prof.get("header") or {}
    header: dict[str, object] = {}
    for field, spec in hdr_spec.items():
        if field in ("decl_ru_parts", "decl_ru"):
            continue
        header[field] = _value(root, spec, skip)
    if prof.get("slot") == "ru_dt":
        header["decl_ru"] = _decl_ru(root, hdr_spec, skip, text)

    # контейнеры
    cspec = prof.get("containers") or {}
    ctags = cspec.get("tags") or []
    head_containers = _find_all(root, ctags, skip)
    link: dict[str, list[str]] = {}
    for blk in [el for el in root.iter() if _local(el.tag) in (cspec.get("block") or [])]:
        cid = _find(blk, ctags)
        goods = _find(blk, cspec.get("goods_link") or [])
        if cid and goods:
            for g in re.split(r"[,，;\s]+", goods):
                if g:
                    link.setdefault(g.lstrip("0") or "0", []).append(cid)

    # контракт из представленных документов
    pd_spec = prof.get("presented_docs") or {}

    def contracts_in(scope, sk=None):
        out = []
        for el in (list(_iter_skip(scope, sk)) if sk else list(scope.iter())):
            if _local(el.tag) in (pd_spec.get("tag") or []):
                code = _find(el, pd_spec.get("code") or [])
                num = _find(el, pd_spec.get("number") or [])
                if num and code in [str(c) for c in pd_spec.get("contract_codes") or []] and num not in out:
                    out.append(num)
        return out

    head_contracts = contracts_in(root, skip) if pd_spec else []

    rows = []
    for idx, item in enumerate(items or [None], start=1):
        rec = dict(header)
        if item is not None:
            for field, spec in ((prof.get("items") or {}).get("fields") or {}).items():
                rec[field] = _value(item, spec)
            item_containers = _find_all(item, ctags) if cspec.get("in_items") else []
            gno = str(rec.get("item_no") or idx).lstrip("0") or "0"
            conts = item_containers or link.get(gno) or head_containers
            rec["container"] = "; ".join(conts)
            ic = contracts_in(item) if pd_spec else []
            if pd_spec:
                rec["contract"] = " / ".join(ic or head_contracts)
        else:
            rec["container"] = "; ".join(head_containers)
            if pd_spec:
                rec["contract"] = " / ".join(head_contracts)
        rows.append(rec)

    df = pd.DataFrame(rows)
    # перекодировки и типы
    if "currency" in df.columns:
        df["currency"] = df["currency"].map(lambda v: _map(v, cmaps.get("currency")))
    if "unit" in df.columns:
        df["unit"] = df["unit"].map(lambda v: _map(v, cmaps.get("unit")))
    if "first_unit" in df.columns:
        df["first_unit"] = df["first_unit"].map(lambda v: _map(v, cmaps.get("unit")))
        if "net_weight" not in df.columns or df["net_weight"].map(lambda v: norm_text(v) == "").all():
            df["net_weight"] = [q if norm_unit(u) == "kg" else (norm_number(q) * 1000 if norm_unit(u) == "t" else None)
                                for q, u in zip(df.get("first_qty", [None] * len(df)), df["first_unit"])]
    if "incoterms" in df.columns:
        code = df["incoterms"]
        df["incoterms"] = code.map(lambda v: CN_TRANS_MODE.get(str(v).strip(), v))
        if (code.astype(str) != df["incoterms"].astype(str)).any():
            df["incoterms_code"] = code          # код 成交方式 показываем, только если он отличается от условия
    for col in ("decl_date", "ie_date"):
        if col in df.columns:
            df[col] = df[col].map(lambda v: norm_date(v) or v)
    if "hs_code" in df.columns:
        df["hs_code"] = df["hs_code"].map(norm_hs)
    for col in df.columns:
        if col in NUMERIC_FIELDS:
            df[col] = df[col].map(lambda v: norm_number(v) if norm_text(v) else None)
    df = df.loc[:, [c for c in df.columns if not df[c].map(lambda v: norm_text(v) == "").all()]]
    meta = {"profile": pid, "title": prof.get("title", pid), "slot": prof.get("slot"),
            "items": len(items), "file": filename,
            "decl": header.get("decl_ru") or header.get("decl_cn") or ""}
    return df, meta


def is_xml(content: bytes, filename: str = "") -> bool:
    if filename.lower().endswith(".xml"):
        return True
    head = content[:300].lstrip(b"\xef\xbb\xbf \r\n\t")
    return head.startswith(b"<?xml") or head.startswith(b"<")


def read_many(files: list[tuple[bytes, str]]) -> tuple[pd.DataFrame, list[dict]]:
    """Несколько XML (одна декларация = один файл) -> одна таблица."""
    frames, metas = [], []
    for content, name in files:
        df, meta = parse_xml(content, name)
        df.insert(0, "source_file", name)
        frames.append(df)
        metas.append(meta)
    if not frames:
        return pd.DataFrame(), []
    return pd.concat(frames, ignore_index=True, sort=False), metas


def to_bytes_io(content: bytes) -> io.BytesIO:
    return io.BytesIO(content)
