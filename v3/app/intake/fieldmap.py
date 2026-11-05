from __future__ import annotations
# -*- coding: utf-8 -*-
"""Подпись в документе -> поле единого словаря (config/fields.yaml).

«38. Вес нетто», «净重», «Net weight, kg», «44. Контракт №» — номер графы и «№»
в конце отбрасываются, дальше ищется точное совпадение с синонимом словаря,
а если его нет — самый длинный синоним, который целиком входит в подпись
(«Net weight, kg» -> net_weight). Так новая подпись подключается синонимом
в fields.yaml, без кода.
"""
import functools
import os
import re
import unicodedata

from app import fields as fdict

_LEAD = re.compile(r"^\s*(?:графа|гр\.|box|栏)?\s*\d{1,3}\s*[.)\-:]?\s*(?=\D)", re.I)
_TRAIL = re.compile(r"(?:\s+(?:no\.?|n°)|\s*[№#])?[\s:：\-–—]*$", re.I)
_CJK = re.compile(r"[⺀-鿿豈-﫿]")


def _mtime() -> float:
    return os.path.getmtime(fdict.FIELDS_PATH) if os.path.exists(fdict.FIELDS_PATH) else 0.0


EXTRACT_PATH = os.path.join(fdict.PROJECT_ROOT, "config", "intake", "extract.yaml")


def _extra_mtime() -> float:
    return os.path.getmtime(EXTRACT_PATH) if os.path.exists(EXTRACT_PATH) else 0.0


@functools.lru_cache(maxsize=4)
def _index(_mt: float, _mt2: float = 0.0) -> tuple[dict, list]:
    import yaml
    idx = dict(fdict.synonyms_index())
    if os.path.exists(EXTRACT_PATH):
        with open(EXTRACT_PATH, encoding="utf-8") as fh:
            extra = (yaml.safe_load(fh) or {}).get("extra_synonyms") or {}
        for field, names in extra.items():
            for name in names or []:
                idx.setdefault(fdict.header_key(name), field)
    keys = sorted((k for k in idx if len(k) >= (2 if _CJK.search(k) else 4)), key=len, reverse=True)
    return idx, keys


def index() -> tuple[dict, list]:
    return _index(_mtime(), _extra_mtime())


def clean_label(label, trail: bool = True) -> str:
    s = unicodedata.normalize("NFKC", str(label or "")).replace("\xa0", " ").strip()
    s = _LEAD.sub("", s)
    if trail:
        s = _TRAIL.sub("", s)
    return s.strip(" .:\uff1a-\u2013\u2014\t")


def field_for_label(label, min_cover: float = 0.4) -> tuple[str | None, str]:
    """-> (поле, как найдено: «exact» | «partial:<синоним>») или (None, "")."""
    lab = clean_label(label)
    if not lab or len(lab) > 60:
        return None, ""
    key = fdict.header_key(lab)
    if not key or key.isdigit():
        return None, ""
    idx, keys = index()
    for k in (fdict.header_key(clean_label(label, trail=False)), key):   # «Item No» — сам синоним
        if k in idx:
            return idx[k], "exact"
    for k in keys:
        if k in key and len(k) / len(key) >= min_cover:
            return idx[k], f"partial:{k}"
    return None, ""


def find_header(grid: list, max_rows: int = 30) -> tuple[int | None, dict]:
    """Строка заголовков таблицы: -> (номер строки, {номер колонки: поле}) или (None, {}).

    Заголовок — строка, где не меньше двух ячеек — подписи полей словаря и такие
    подписи составляют не меньше 60 % заполненных ячеек (так строка
    «Валюта | CNY | Условия | CPT» не считается заголовком).
    """
    best = (0, None, {})
    for r in range(min(len(grid), max_rows)):
        row = grid[r]
        filled = [c for c in row if str(c).strip()]
        if len(filled) < 2:
            continue
        mapping: dict[int, str] = {}
        for c, cell in enumerate(row):
            if not cell or len(str(cell)) > 80:
                continue
            f, _ = field_for_label(cell, min_cover=0.4)
            if f and f not in mapping.values():
                mapping[c] = f
        if len(mapping) >= 2 and len(mapping) / len(filled) >= 0.6 and len(mapping) > best[0]:
            best = (len(mapping), r, mapping)
    return (best[1], best[2]) if best[1] is not None else (None, {})
