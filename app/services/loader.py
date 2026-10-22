from __future__ import annotations
# -*- coding: utf-8 -*-
"""Загрузчик источников данных.

Принимает файлы (xlsx/xls/csv) по слотам из config/system.yaml и
возвращает dict[str, Source]. Если источник помечен `builtin:` и не
загружен пользователем — подставляется встроенный справочник.
"""
import os
import pandas as pd
from app.models import Source

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _read_any(path: str) -> pd.DataFrame:
    low = path.lower()
    if low.endswith(".csv"):
        return pd.read_csv(path, dtype=object)
    if low.endswith((".xlsx", ".xls")):
        return pd.read_excel(path, dtype=object)
    raise ValueError(f"Неподдерживаемый формат файла: {path}")


def read_upload(fileobj, filename: str) -> pd.DataFrame:
    """Прочитать загруженный через API файл (file-like)."""
    low = filename.lower()
    if low.endswith(".csv"):
        return pd.read_csv(fileobj, dtype=object)
    if low.endswith((".xlsx", ".xls")):
        return pd.read_excel(fileobj, dtype=object)
    raise ValueError(f"Неподдерживаемый формат файла: {filename}")


def load_sources(uploaded: dict[str, tuple[object, str]] | None = None,
                 cfg: dict | None = None) -> dict[str, Source]:
    """Собрать источники.

    uploaded — {slot: (fileobj, filename)} для загруженных файлов.
    Для слотов с `builtin:` без загрузки берётся встроенный файл.
    """
    uploaded = uploaded or {}
    sources: dict[str, Source] = {}

    for slot, scfg in (cfg or {}).get("sources", {}).items():
        if slot in uploaded:
            fileobj, filename = uploaded[slot]
            df = read_upload(fileobj, filename)
            sources[slot] = Source(slot=slot, name=filename, df=df)
            continue

        builtin = scfg.get("builtin")
        if builtin:
            path = os.path.join(PROJECT_ROOT, builtin)
            if os.path.exists(path):
                df = _read_any(path)
                sources[slot] = Source(slot=slot, name=f"[builtin] {builtin}", df=df)

    return sources
