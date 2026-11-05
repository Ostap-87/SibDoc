from __future__ import annotations
# -*- coding: utf-8 -*-
"""Единый словарь полей (config/fields.yaml): подписи RU/EN/ZH и синонимы колонок.

Зачем: одна и та же сущность (контейнер, номер ДТ, вес нетто…) в выгрузках
SAP, FineBI, программ брокеров РФ и Китая называется по-разному. Загрузчик
переименовывает распознанные колонки в имена, которые ждут проверки
(config/system.yaml -> sources.<slot>.fields), поэтому новая база
подключается добавлением синонима, а не правкой кода.
"""
import functools
import os
import re
import unicodedata

import pandas as pd
import yaml

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
FIELDS_PATH = os.path.join(PROJECT_ROOT, "config", "fields.yaml")
LANGS = ("ru", "en", "zh")


def header_key(name) -> str:
    """Ключ сравнения названий колонок: без регистра, пробелов, знаков препинания."""
    s = unicodedata.normalize("NFKC", str(name or "")).lower()
    return re.sub(r"[\s_\-–—.,:;()（）\[\]{}/\\|'\"№#*]+", "", s)


@functools.lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> dict:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data.get("fields") or {}


def dictionary() -> dict:
    """{field: {ru, en, zh, type, synonyms}} — перечитывается при изменении файла."""
    if not os.path.exists(FIELDS_PATH):
        return {}
    return _load(FIELDS_PATH, os.path.getmtime(FIELDS_PATH))


def label(field: str, lang: str = "ru") -> str:
    rec = dictionary().get(field) or {}
    return rec.get(lang) or rec.get("ru") or field


def synonyms_index() -> dict[str, str]:
    """{header_key(синоним): field}. Ранее объявленные поля выигрывают при совпадении."""
    idx: dict[str, str] = {}
    for field, rec in dictionary().items():
        for name in [field, rec.get("ru"), rec.get("en"), rec.get("zh"), *(rec.get("synonyms") or [])]:
            if name:
                idx.setdefault(header_key(name), field)
    return idx


def field_of_column(col: str, slot_fields: dict | None = None) -> str | None:
    """Какое поле словаря соответствует колонке (с учётом карты слота)."""
    for field, expected in (slot_fields or {}).items():
        if str(expected) == str(col):
            return field
    return synonyms_index().get(header_key(col))


def column_label(col: str, slot_fields: dict | None = None, lang: str = "ru") -> str:
    field = field_of_column(col, slot_fields)
    return label(field, lang) if field else str(col)


def bilingual_label(col: str, slot_fields: dict | None = None) -> str:
    """«Номер контейнера / 集装箱号» для отчётов, которые читают обе стороны."""
    field = field_of_column(col, slot_fields)
    if not field:
        return str(col)
    ru, zh = label(field, "ru"), label(field, "zh")
    return ru if ru == zh else f"{ru} / {zh}"


def map_columns(df: pd.DataFrame, slot_fields: dict | None) -> tuple[pd.DataFrame, list[dict]]:
    """Переименовать распознанные колонки в ожидаемые имена слота.

    slot_fields — {field: ожидаемое_имя_колонки}. Колонка переименовывается,
    только если ожидаемой ещё нет. Возвращает (df, отчёт о переименованиях).
    """
    if not slot_fields or df is None or df.empty and not len(df.columns):
        return df, []
    rec = dictionary()
    present = set(map(str, df.columns))
    by_key: dict[str, str] = {}
    for c in df.columns:
        by_key.setdefault(header_key(c), c)
    renames: dict[str, str] = {}
    report: list[dict] = []
    for field, expected in slot_fields.items():
        expected = str(expected)
        if expected in present:
            continue
        names = [field, *(rec.get(field, {}).get("synonyms") or []),
                 rec.get(field, {}).get("ru"), rec.get(field, {}).get("en"), rec.get(field, {}).get("zh")]
        for name in names:
            if not name:
                continue
            src = by_key.get(header_key(name))
            if src is not None and src not in renames and str(src) not in slot_fields.values():
                renames[src] = expected
                report.append({"field": field, "from": str(src), "to": expected})
                break
    if renames:
        df = df.rename(columns=renames)
    return df, report


def canonicalize(df: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """Для новых слотов (ru_dt, cn_dec): все распознанные колонки -> имя поля словаря."""
    idx = synonyms_index()
    renames, report, used = {}, [], set(map(str, df.columns))
    for c in df.columns:
        field = idx.get(header_key(c))
        if field and field != c and field not in used and field not in renames.values():
            renames[c] = field
            report.append({"field": field, "from": str(c), "to": field})
    return (df.rename(columns=renames) if renames else df), report


def public() -> list[dict]:
    """Словарь для интерфейса."""
    return [{"field": f, "ru": r.get("ru"), "en": r.get("en"), "zh": r.get("zh"),
             "type": r.get("type", "text"), "synonyms": r.get("synonyms") or []}
            for f, r in dictionary().items()]
