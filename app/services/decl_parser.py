from __future__ import annotations
# -*- coding: utf-8 -*-
"""Парсер OCR-текста сканированных деклараций.

Извлекает параметры из «секций» документа (шапка, товарная часть,
транспорт) по паттернам трёх языков — RU / EN / ZH.

Все паттерны живут в config/scan_fields.yaml и расширяются без кода.
Каждое поле возвращается со статусом:
  found    — значение извлечено и прошло валидацию;
  review   — извлечено, но выглядит сомнительно (OCR-ошибка?);
  missing  — в тексте не найдено.
"""
import re
from typing import Any

import yaml
import os

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

FIELD_LABELS = {
    "decl_num":        {"ru": "Номер декларации КНР", "en": "CN declaration No.", "zh": "报关单号"},
    "decl_ru":         {"ru": "Номер ДТ РФ",      "en": "RU DT No.",       "zh": "俄罗斯报关单号"},
    "container_num":   {"ru": "Контейнер",        "en": "Container",       "zh": "集装箱号"},
    "contract":        {"ru": "Контракт",         "en": "Contract",        "zh": "合同号"},
    "incoterms":       {"ru": "Инкотермс",        "en": "Incoterms",       "zh": "贸易术语"},
    "hs_code":         {"ru": "Код ТН ВЭД",       "en": "HS code",         "zh": "商品编码"},
    "unit_price":      {"ru": "Цена за единицу",  "en": "Unit price",      "zh": "单价"},
    "net_weight":      {"ru": "Вес нетто",        "en": "Net weight",      "zh": "净重"},
    "declared_model":  {"ru": "Заявленная модель","en": "Declared model",  "zh": "申报型号"},
}

DEFAULT_PATTERNS: dict[str, list[str]] = {
    "decl_ru": [
        r"(\d{8}\s*/\s*\d{6}\s*/\s*[A-ZА-Я]?\d{7})",
    ],
    "decl_num": [
        r"(?:ДТ|декларац\w*\s+на\s+товар\w*)[ №:\-]*(\d{18,25})",
        r"(?:declaration\s*(?:no\.?|number)|customs\s*decl\w*)[ №:\-]*(\d{18,25})",
        r"(?:报关\s*单\s*号|海关\s*编号)[ №:\-：:]*(\d{18,25})",
        r"(?<![\d/])(\d{18})(?![\d/])",
    ],
    "container_num": [
        r"\b([A-ZА-Я]{4}\s?\d{7})\b",
    ],
    "contract": [
        r"(?:контракт|contract|合\s*同\s*号?)\s*(?:№|no\.?|N[°º])?\s*[:\-：]?\s*([A-ZА-ЯЁA-Z0-9\.\-_/]{4,40})",
    ],
    "incoterms": [
        r"\b(CIF|C&F|CFR|FOB|C&I|EXW|DAP|DDP|FCA|CPT)\b",
        r"(?:условия\s*поставки|terms\s*of\s*delivery|incoterms?|贸易术语|成交方式)\s*[:\-：]?\s*([A-Z&А-ЯЁ]{2,5})",
    ],
    "hs_code": [
        r"(?:код\s*ТН\s*ВЭД|код\s*TH\s*ВЭД|товарная\s*позиция)[ №:\-：:]*(\d[\d\s]{7,11}\d)",
        r"(?:HS\s*code|commodity\s*code)[ №:\-：:]*(\d[\d\s]{7,11}\d)",
        r"(?:商\s*品\s*编\s*码|税\s*则\s*号\s*列)[ №:\-：:]*(\d[\d\s]{7,11}\d)",
    ],
    "unit_price": [
        r"(?:цена\s*за\s*единиц\w*|цена)[ №:\-：:]*([\d\s]{1,12}[,\.]\d{1,4})",
        r"(?:unit\s*price|price)[ №:\-：:]*([\d\s]{1,12}[,\.]\d{1,4})",
        r"(?:单\s*价)[ №:\-：:]*([\d\s]{1,12}[,\.]\d{1,4})",
    ],
    "net_weight": [
        r"(?:вес\s*нетто)[ №:\-：:]*([\d\s]{1,12}[,\.]?\d{0,3})",
        r"(?:net\s*weight)[ №:\-：:]*([\d\s]{1,12}[,\.]?\d{0,3})",
        r"(?:净\s*重)[ №:\-：:]*([\d\s]{1,12}[,\.]?\d{0,3})",
    ],
    "declared_model": [
        r"(?:модель|заявленная\s*модель)[ №:\-：:]*([A-ZА-ЯЁ0-9][A-ZА-ЯЁ0-9\.\-_/ ]{1,40})",
        r"(?:model|规\s*格\s*型\s*号|型\s*号)[ №:\-：:]*([A-ZА-ЯЁ0-9][A-ZА-ЯЁ0-9\.\-_/ ]{1,40})",
    ],
}

# OCR часто путает кириллицу и латиницу в номерах контейнеров
_CYR2LAT = str.maketrans("АВЕКМНОРСТУХЗ", "ABEKMHOPCTYX3")

# Деградированные OCR-прочтения инкотермс → каноническое значение.
# Маппятся только токены, которые сами не являются валидным инкотермс,
# поэтому ложных исправлений быть не может. Расширяется через
# config/scan_fields.yaml → ocr_fixes.
DEFAULT_OCR_FIXES: dict[str, dict[str, str]] = {
    "incoterms": {
        "CE": "CIF", "C1F": "CIF", "CLF": "CIF", "CIF": "CIF",
        "F0B": "FOB", "FO8": "FOB", "FOE": "FOB",
        "CFK": "CFR", "CKR": "CFR",
        "EXY": "EXW", "EXV": "EXW",
        "ECA": "FCA", "FGA": "FCA", "FCÁ": "FCA", "F CA": "FCA",
        "CPI": "CPT", "GPT": "CPT", "CP7": "CPT",
        "DAR": "DAP", "0AP": "DAP",
    },
}


def load_patterns(cfg: dict | None = None) -> dict[str, list[str]]:
    path = os.path.join(PROJECT_ROOT, "config", "scan_fields.yaml")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        merged = {k: list(v) for k, v in DEFAULT_PATTERNS.items()}
        for field, pats in (data.get("fields") or {}).items():
            merged[field] = list(pats)
        return merged
    return DEFAULT_PATTERNS


def load_ocr_fixes() -> dict[str, dict[str, str]]:
    """Карты исправления OCR-прочтений (defaults + config/scan_fields.yaml)."""
    fixes = {f: dict(m) for f, m in DEFAULT_OCR_FIXES.items()}
    path = os.path.join(PROJECT_ROOT, "config", "scan_fields.yaml")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        for field, m in (data.get("ocr_fixes") or {}).items():
            fixes.setdefault(field, {}).update({str(k).upper(): str(v).upper() for k, v in (m or {}).items()})
    return fixes


def _clean_number(raw: str) -> str:
    return re.sub(r"\s", "", raw).replace(",", ".")


def _validate(field: str, value: str) -> str:
    """found | review по простым правилам валидности."""
    if field == "container_num":
        return "found" if re.fullmatch(r"[A-Z]{4}\d{7}", value) else "review"
    if field == "decl_num":
        return "found" if re.fullmatch(r"\d{18}", value) else "review"
    if field == "decl_ru":
        return "found" if re.fullmatch(r"\d{8}/\d{6}/[A-Z]?\d{7}", value) else "review"
    if field == "hs_code":
        digits = re.sub(r"\D", "", value)
        return "found" if 8 <= len(digits) <= 10 else "review"
    if field in ("unit_price", "net_weight"):
        try:
            v = float(value)
            return "found" if v > 0 else "review"
        except ValueError:
            return "review"
    if field == "incoterms":
        return "found" if value.upper() in (
            "CIF", "C&F", "CFR", "FOB", "C&I", "EXW", "DAP", "DDP", "FCA", "CPT") else "review"
    return "found"


def parse_declaration(text: str, cfg: dict | None = None) -> dict[str, dict[str, Any]]:
    """Разобрать OCR-текст декларации на поля.

    Возвращает {field: {value, status, matched_by}}.
    """
    patterns = load_patterns(cfg)
    result: dict[str, dict[str, Any]] = {}

    for field, pats in patterns.items():
        hit = None
        for pat in pats:
            m = re.search(pat, text, re.IGNORECASE | re.UNICODE)
            if m:
                hit = (m.group(1).strip(), pat)
                break
        if not hit:
            result[field] = {"value": "", "status": "missing", "matched_by": ""}
            continue

        value, pat = hit
        if field == "container_num":
            value = value.translate(_CYR2LAT).replace(" ", "").upper()
        elif field == "decl_ru":
            value = re.sub(r"\s", "", value).translate(_CYR2LAT)
        elif field in ("decl_num", "hs_code"):
            value = _clean_number(value)
        elif field in ("unit_price", "net_weight"):
            value = _clean_number(value)
        elif field == "declared_model":
            value = re.sub(r"\s{2,}", " ", value).strip()
        elif field == "incoterms":
            value = value.translate(_CYR2LAT).upper()
            value = load_ocr_fixes().get("incoterms", {}).get(value, value)

        result[field] = {
            "value": value,
            "status": _validate(field, value),
            "matched_by": pat,
        }
    return result


def fields_summary(parsed: dict[str, dict[str, Any]], lang: str = "ru") -> list[dict]:
    """Плоский список для UI/API с локализованными подписями полей."""
    out = []
    for field, rec in parsed.items():
        label = FIELD_LABELS.get(field, {}).get(lang) or FIELD_LABELS.get(field, {}).get("en", field)
        out.append({"field": field, "label": label, **{k: rec[k] for k in ("value", "status")}})
    return out
