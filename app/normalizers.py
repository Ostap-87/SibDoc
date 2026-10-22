from __future__ import annotations
# -*- coding: utf-8 -*-
"""Normalizers — единая точка очистки значений перед сравнением.

Любая проверка использует ТОЛЬКО эти функции, чтобы правила
нормализации не расходились между проверками. Значение для показа
в отчёте остаётся исходным, нормализуется только ключ сравнения.
"""
import math
import re
import unicodedata

import numpy as np
import pandas as pd

# Кириллические буквы, которые выглядят как латинские (в номерах контейнеров,
# контрактов, деклараций их путают при наборе и при OCR).
_HOMOGLYPHS = str.maketrans({
    "А": "A", "В": "B", "Е": "E", "Ё": "E", "К": "K", "М": "M", "Н": "H",
    "О": "O", "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X", "З": "3",
    "І": "I", "Ј": "J", "Ѕ": "S",
})

# Валидные условия поставки (Incoterms 2020 + 2010 + устаревшие, встречающиеся в документах)
INCOTERMS = ("EXW", "FCA", "FAS", "FOB", "CFR", "CIF", "CPT", "CIP", "DAP", "DPU",
             "DDP", "DAT", "DAF", "DES", "DEQ", "DDU")

# Синонимы по умолчанию; дополняются config/rules/incoterms.yaml -> synonyms
INCOTERMS_SYNONYMS = {
    "C&F": "CFR", "C+F": "CFR", "CNF": "CFR", "C AND F": "CFR", "C/F": "CFR",
    # китайские названия (成交方式)
    "成本加运费": "CFR", "到岸价": "CIF", "成本加保险费加运费": "CIF",
    "离岸价": "FOB", "船上交货": "FOB", "工厂交货": "EXW", "货交承运人": "FCA",
    "运费付至": "CPT", "运费和保险费付至": "CIP", "目的地交货": "DAP",
    "完税后交货": "DDP",
}

# Коды «成交方式» таможни КНР
CN_TRANS_MODE = {"1": "CIF", "2": "CFR", "3": "FOB", "4": "C&I", "5": "市场价",
                 "6": "垫仓", "7": "EXW"}

_UNITS = {
    "kg": ("kg", "кг", "kgs", "килограмм", "千克", "公斤", "035"),
    "t": ("t", "т", "тн", "тонна", "тонн", "ton", "tons", "tonne", "mt", "吨", "070"),
    "g": ("g", "г", "грамм", "克", "036"),
    "lb": ("lb", "lbs", "磅"),
}


# --------------------------------------------------------------------- базовое
def is_blank(v) -> bool:
    """Пусто: None, NaN, пустая строка, «nan», «-»."""
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    try:
        if pd.isna(v):
            return True
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    return s == "" or s.lower() in ("nan", "none", "null", "-", "—", "n/a")


def nfkc(v) -> str:
    """Юникод-нормализация: полноширинные «ＣＩＦ１２» -> «CIF12», неразрывные пробелы -> пробел."""
    if is_blank(v):
        return ""
    return unicodedata.normalize("NFKC", str(v)).replace(" ", " ")


def norm_text(v) -> str:
    """Произвольный текст: схлопнуть пробелы, обрезать."""
    if is_blank(v):
        return ""
    return re.sub(r"\s+", " ", nfkc(v)).strip()


def latinize(v) -> str:
    """Верхний регистр + кириллица-«двойники» -> латиница (СХ -> CX)."""
    return nfkc(v).upper().translate(_HOMOGLYPHS)


def has_mixed_scripts(v) -> bool:
    """В одной строке и кириллица, и латиница — частый признак опечатки в ключе."""
    s = nfkc(v)
    return bool(re.search(r"[А-Яа-яЁё]", s)) and bool(re.search(r"[A-Za-z]", s))


# --------------------------------------------------------------------- числа
def norm_number(v) -> float:
    """Число из любого формата -> float; иначе NaN.

    9661.0 | '9 661' | '9661,00' | '1 234,56' | '1,234.56' | '0,00' | '1.234.567'.
    Одна запятая без точки — десятичный разделитель (русская запись).
    """
    if is_blank(v):
        return np.nan
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float, np.integer, np.floating)):
        return float(v)
    s = nfkc(v).replace(" ", "").replace("'", "")
    s = re.sub(r"[^\d,.\-eE+]", "", s)
    if not re.search(r"\d", s):
        return np.nan
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif s.count(",") > 1:
        s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return np.nan


# совместимость: прежнее имя
norm_price = norm_number


def is_zero_or_blank(v) -> bool:
    """Пусто или ноль в любой записи («», «0», «0,00», «0.0»)."""
    if is_blank(v):
        return True
    n = norm_number(v)
    return bool(not np.isnan(n) and abs(n) < 1e-12)


def nearly_equal(a: float, b: float, tol_abs: float = 0.0, tol_pct: float = 0.0) -> bool:
    """|a-b| <= max(tol_abs, tol_pct% от большего по модулю)."""
    if a is None or b is None or np.isnan(a) or np.isnan(b):
        return False
    lim = max(tol_abs, abs(tol_pct) / 100.0 * max(abs(a), abs(b)))
    return abs(a - b) <= lim + 1e-9


def scale_cd_price(v: float, per_kg_threshold: float) -> float:
    """Запасное правило, когда единица цены неизвестна: CD декларирует цену
    за кг (9.661), SAP хранит за тонну (9661) — значения ниже порога ×1000."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return v
    return v * 1000 if v < per_kg_threshold else v


def norm_unit(v) -> str:
    """Единица измерения -> kg | t | g | lb | '' (кг, 千克, 吨, тонна, код 166/168 не распознаём)."""
    s = norm_text(v).lower().strip(". ")
    if not s:
        return ""
    for unit, names in _UNITS.items():
        if s in names:
            return unit
    return ""


def price_per_ton(price: float, unit: str, per_kg_threshold: float = 100) -> tuple[float, str]:
    """Цена за тонну. -> (значение, как получено: 'unit:kg' | 'unit:t' | 'threshold' | 'as_is')."""
    if price is None or np.isnan(price):
        return np.nan, ""
    if unit == "kg":
        return price * 1000, "unit:kg"
    if unit == "g":
        return price * 1_000_000, "unit:g"
    if unit == "t":
        return price, "unit:t"
    if unit == "lb":
        return price / 0.45359237 * 1000, "unit:lb"
    if per_kg_threshold and price < per_kg_threshold:
        return price * 1000, "threshold"
    return price, "as_is"


# --------------------------------------------------------------------- ключи
def norm_container(v) -> str:
    """Номер контейнера: латиница, верхний регистр, без пробелов и дефисов.
    'smgu 8851229' -> 'SMGU8851229', кириллические «двойники» -> латиница."""
    if is_blank(v):
        return ""
    return re.sub(r"[\s\-_./]+", "", latinize(v))


def container_check_digit_ok(c: str) -> bool:
    """Контрольная цифра ISO 6346 (последняя цифра номера контейнера)."""
    c = norm_container(c)
    if not re.fullmatch(r"[A-Z]{4}\d{7}", c):
        return False
    vals = {}
    n = 10
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if n % 11 == 0:
            n += 1
        vals[ch] = n
        n += 1
    total = sum((vals[ch] if ch.isalpha() else int(ch)) * (2 ** i) for i, ch in enumerate(c[:10]))
    return (total % 11) % 10 == int(c[10])


def norm_hs(v) -> str:
    """Код ТН ВЭД / HS: только цифры. '400220 1000' -> '4002201000'. Число 4002191300.0 -> '4002191300'."""
    if is_blank(v):
        return ""
    if isinstance(v, (float, np.floating)) and float(v).is_integer():
        v = int(v)
    return re.sub(r"\D", "", nfkc(v))


_RU_DT = re.compile(r"(\d{8})\s*[/\\|\-]\s*(\d{6})\s*[/\\|\-]\s*([A-ZА-Я]?\d{7})")


def norm_decl(v) -> str:
    """Номер декларации для сравнения.

    ДТ РФ «10511010/020126/5000123» (любой разделитель) -> «10511010/020126/5000123»;
    报关单 (18 цифр) -> только цифры; прочее -> латиница без пробелов.
    """
    if is_blank(v):
        return ""
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    if isinstance(v, (float, np.floating)):
        return str(int(v)) if float(v).is_integer() else str(v)
    s = latinize(v)
    m = _RU_DT.search(s)
    if m:
        return f"{m.group(1)}/{m.group(2)}/{m.group(3)}"
    if re.fullmatch(r"[\d\s\-]+", s):
        return re.sub(r"\D", "", s)
    if re.fullmatch(r"\d+(\.\d+)?E\+?\d+", s):          # 2.21820261E+17 из Excel
        try:
            return str(int(float(s)))
        except ValueError:
            pass
    return re.sub(r"\s+", "", s)


def decl_is_lossy(v) -> bool:
    """Номер декларации пришёл числом с плавающей точкой (Excel хранит до 15 значащих цифр):
    последние цифры 18-значного номера могли потеряться."""
    if isinstance(v, (float, np.floating)) and not np.isnan(v):
        return abs(v) >= 1e15
    s = nfkc(v).strip().upper()
    return bool(re.fullmatch(r"\d+(\.\d+)?E\+?\d+", s))


def ru_dt_date(v) -> str:
    """Дата из номера ДТ РФ: «10511010/020126/5000123» -> «2026-01-02»; иначе ''."""
    m = _RU_DT.search(latinize(v)) if not is_blank(v) else None
    if not m:
        return ""
    d = m.group(2)
    return f"20{d[4:6]}-{d[2:4]}-{d[0:2]}"


# --------------------------------------------------------------------- контракты
DEFAULT_CONTRACT_IGNORE = " \t-–—"


def canon_contract(v, ignore_chars: str | None = None) -> str:
    """Контракт для сравнения: латиница, верхний регистр, без пробелов/дефисов
    (и без символов из правила ignore_chars)."""
    s = latinize(v)
    chars = set(DEFAULT_CONTRACT_IGNORE if ignore_chars is None else ignore_chars) | {" ", "\t"}
    chars.discard("\\")
    return "".join(ch for ch in s if ch not in chars)


_GLUE_SPLIT = re.compile(r"\s*[/;,|+]\s*|\s+и\s+|\s*&\s*(?=[A-ZА-ЯЁ]{2,})")
_NEXT_CONTRACT = re.compile(r"[\s\-–—]+(?=[A-ZА-ЯЁ]{2,8}[.\s]?\d)")


def split_contracts(v) -> list[str]:
    """«Склеенный» контракт -> части.

    'СХ.35105-396В/СХ.35105-302В' -> ['СХ.35105-396В', 'СХ.35105-302В'];
    'SISH.12235-SISH.12236' -> ['SISH.12235', 'SISH.12236'];
    'СХ.35102-301В' (основной + доп. соглашение) остаётся одним контрактом.
    """
    s = norm_text(v)
    if not s:
        return []
    parts: list[str] = []
    for chunk in _GLUE_SPLIT.split(s):
        chunk = chunk.strip(" -–—")
        if not chunk:
            continue
        parts.extend(p.strip(" -–—") for p in _NEXT_CONTRACT.split(chunk) if p.strip(" -–—"))
    return parts


# --------------------------------------------------------------------- условия поставки
def norm_incoterms(v, synonyms: dict | None = None) -> tuple[str, str]:
    """-> (условие, место). 'CIF QINGDAO' -> ('CIF', 'QINGDAO'); 'C&F' -> ('CFR', '');
    '成本加运费' -> ('CFR', ''); код 成交方式 '2' -> ('CFR', '')."""
    s = norm_text(v)
    if not s:
        return "", ""
    if s in CN_TRANS_MODE:
        return CN_TRANS_MODE[s], ""
    syn = {k.upper(): str(val).upper() for k, val in {**INCOTERMS_SYNONYMS, **(synonyms or {})}.items()}
    up = latinize(s)
    orig = nfkc(s).upper()          # та же длина, что up: место показываем без замены букв
    # синонимы — сначала самые длинные
    for key in sorted(syn, key=len, reverse=True):
        idx = up.find(key)
        if idx >= 0 and (key[0] > "⺀" or _token_at(up, idx, len(key))):
            place = (orig[:idx] + " " + orig[idx + len(key):]).strip(" ,.-:")
            return syn[key], re.sub(r"\s+", " ", place)
    for term in INCOTERMS:
        m = re.search(rf"(?<![A-Z]){term}(?![A-Z])", up)
        if m:
            place = (orig[:m.start()] + " " + orig[m.end():]).strip(" ,.-:")
            return term, re.sub(r"\s+", " ", place)
    return up, ""


def _token_at(s: str, idx: int, ln: int) -> bool:
    before = s[idx - 1] if idx > 0 else " "
    after = s[idx + ln] if idx + ln < len(s) else " "
    return not before.isalpha() and not after.isalpha()


# --------------------------------------------------------------------- модели / марки
def canon_model(v) -> str:
    """Марка/модель для сравнения: латиница, без пробелов, дефисов, точек и подчёркиваний.
    'BR-1203 Ti' == 'BR 1203 TI' == 'BR1203Ti'."""
    return re.sub(r"[\s\-–—_.·/]+", "", latinize(v))


def norm_date(v) -> str:
    """Дата -> 'YYYY-MM-DD' или ''. Понимает 03.01.2026, 2026-01-03, 20260103, Excel-дату."""
    if is_blank(v):
        return ""
    if isinstance(v, (pd.Timestamp,)):
        return v.strftime("%Y-%m-%d")
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    s = norm_text(v)
    for rx, order in ((r"^(\d{4})-(\d{1,2})-(\d{1,2})", "ymd"), (r"^(\d{1,2})[./](\d{1,2})[./](\d{4})", "dmy"),
                      (r"^(\d{4})(\d{2})(\d{2})$", "ymd"), (r"^(\d{4})/(\d{1,2})/(\d{1,2})", "ymd"),
                      (r"^(\d{4})年(\d{1,2})月(\d{1,2})日", "ymd")):
        m = re.match(rx, s)
        if m:
            a, b, c = m.groups()
            y, mo, d = (a, b, c) if order == "ymd" else (c, b, a)
            try:
                return pd.Timestamp(int(y), int(mo), int(d)).strftime("%Y-%m-%d")
            except ValueError:
                return ""
    try:
        return pd.to_datetime(s, dayfirst=True).strftime("%Y-%m-%d")
    except (ValueError, TypeError, OverflowError):
        return ""
