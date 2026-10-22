from __future__ import annotations
# -*- coding: utf-8 -*-
"""Normalizers — единая точка очистки значений перед сравнением.

Любая проверка использует ТОЛЬКО эти функции, чтобы правила
нормализации не расходились между проверками.
"""
import re
import pandas as pd
import numpy as np


def norm_container(v) -> str:
    """Номер контейнера: верхний регистр, без пробелов. 'smgu 8851229' -> 'SMGU8851229'."""
    if pd.isna(v):
        return ""
    return re.sub(r"\s+", "", str(v)).strip().upper()


def norm_price(v) -> float:
    """Цена из любого формата: 9661.0 | '9 661' | '9661,00' -> float; иначе NaN."""
    if pd.isna(v):
        return np.nan
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d,\.\-]", "", str(v)).replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return np.nan


def norm_hs(v) -> str:
    """Код ТН ВЭД: только цифры. '400220 1000' -> '4002201000'."""
    if pd.isna(v):
        return ""
    return re.sub(r"\D", "", str(v))


def norm_text(v) -> str:
    """Произвольный текст: схлопнуть пробелы, обрезать."""
    if pd.isna(v):
        return ""
    return re.sub(r"\s+", " ", str(v)).strip()


def canon_contract(v) -> str:
    """Контракт для сравнения: без пробелов и дефисов, верхний регистр."""
    return re.sub(r"[\s\-–—]", "", norm_text(v)).upper()


def scale_cd_price(v: float, per_kg_threshold: float) -> float:
    """CD декларирует цену за кг (9.661), SAP хранит за тонну (9661).
    Значения ниже порога умножаются на 1000."""
    if pd.isna(v):
        return v
    return v * 1000 if v < per_kg_threshold else v
