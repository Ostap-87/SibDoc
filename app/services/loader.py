from __future__ import annotations
# -*- coding: utf-8 -*-
"""Загрузчик источников данных.

Принимает файлы по слотам из config/system.yaml и возвращает dict[str, Source].

Что умеет:
  - Excel .xlsx/.xlsm/.xls: выбирает лист и строку заголовков, где больше всего
    узнаваемых колонок (или лист из sources.<slot>.sheet);
  - CSV/TXT: кодировка UTF-8 / UTF-8 BOM / GBK (GB18030) / Windows-1251,
    разделитель «,» «;» табуляция «|» — определяются сами;
  - XML деклараций (ДТ ФТС, 报关单) и ZIP с несколькими такими XML;
  - переименование колонок по единому словарю (config/fields.yaml).
Если источник помечен `builtin:` и не загружен — подставляется встроенный справочник.
"""
import csv
import io
import os
import zipfile

import pandas as pd

from app import fields as fdict
from app.models import Source
from app.services import xml_decl

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TABLE_EXTS = (".xlsx", ".xlsm", ".xls", ".csv", ".txt")
ACCEPTED_EXTS = TABLE_EXTS + (".xml", ".zip")
ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "cp1251")
MAX_ZIP_TOTAL = 300 * 1024 * 1024


class LoadError(ValueError):
    pass


# ------------------------------------------------------------------ низкий уровень
def _decode_text(data: bytes) -> tuple[str, str]:
    for enc in ENCODINGS:
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace"), "utf-8 (с заменой символов)"


def _read_csv(data: bytes) -> tuple[pd.DataFrame, dict]:
    text, enc = _decode_text(data)
    sample = text[:20000]
    try:
        delim = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        counts = {d: sample.split("\n", 1)[0].count(d) for d in (",", ";", "\t", "|")}
        delim = max(counts, key=counts.get) if max(counts.values()) else ","
    df = pd.read_csv(io.StringIO(text), dtype=object, sep=delim, keep_default_na=True)
    return df, {"format": "csv", "encoding": enc, "delimiter": {"\t": "TAB"}.get(delim, delim)}


def _header_score(values, wanted_keys: set, syn_keys: set) -> int:
    score = 0
    for v in values:
        k = fdict.header_key(v)
        if not k:
            continue
        if k in wanted_keys:
            score += 3
        elif k in syn_keys:
            score += 1
    return score


def _read_excel(data: bytes, filename: str, slot_cfg: dict) -> tuple[pd.DataFrame, dict]:
    engine = "xlrd" if filename.lower().endswith(".xls") else "openpyxl"
    try:
        book = pd.ExcelFile(io.BytesIO(data), engine=engine)
    except ImportError as exc:
        raise LoadError(f"для {os.path.splitext(filename)[1]} нужен пакет: {exc}")
    wanted = set(map(fdict.header_key, (slot_cfg.get("fields") or {}).values())) | \
        set(map(fdict.header_key, slot_cfg.get("required_columns") or []))
    syn = set(fdict.synonyms_index())
    pinned = slot_cfg.get("sheet")
    sheets = [pinned] if pinned not in (None, "") else book.sheet_names
    best = None
    for sh in sheets:
        try:
            raw = book.parse(sh, header=None, nrows=30, dtype=object)
        except (ValueError, IndexError):
            raise LoadError(f"лист «{sh}» не найден в файле")
        for r in range(min(len(raw), 15)):
            sc = _header_score(raw.iloc[r].tolist(), wanted, syn)
            if best is None or sc > best[0]:
                best = (sc, sh, r)
    if best is None:
        raise LoadError("в файле нет листов с данными")
    _, sheet, hdr = best
    df = book.parse(sheet, header=hdr, dtype=object)
    return df, {"format": "excel", "sheet": str(sheet), "header_row": int(hdr) + 1,
                "sheets": [str(s) for s in book.sheet_names]}


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    empty_unnamed = [c for c in df.columns if c.startswith("Unnamed:") and df[c].isna().all()]
    df = df.drop(columns=empty_unnamed)
    df = df.dropna(how="all")
    df = df.reset_index(drop=True)
    return df


# ------------------------------------------------------------------ публичное API
def read_table(data: bytes, filename: str, slot: str | None = None,
               cfg: dict | None = None) -> tuple[pd.DataFrame, dict]:
    """Байты файла -> (таблица, сведения о чтении). Для слота применяется словарь полей."""
    slot_cfg = ((cfg or {}).get("sources") or {}).get(slot or "", {}) or {}
    low = filename.lower()
    info: dict = {"file": filename}
    if low.endswith(".zip"):
        df, info2 = _read_zip(data, slot, cfg)
        info.update(info2)
    elif xml_decl.is_xml(data, filename) and not low.endswith(TABLE_EXTS):
        df, meta = xml_decl.parse_xml(data, filename)
        df.insert(0, "source_file", filename)
        info.update({"format": "xml", "profile": meta["title"], "declarations": 1,
                     "items": meta["items"]})
        if slot and meta.get("slot") and meta["slot"] != slot:
            info["warning"] = (f"файл похож на «{meta['title']}», а загружен в слот «{slot}»")
    elif low.endswith((".csv", ".txt")):
        df, info2 = _read_csv(data)
        info.update(info2)
    elif low.endswith((".xlsx", ".xlsm", ".xls")):
        df, info2 = _read_excel(data, filename, slot_cfg)
        info.update(info2)
    else:
        raise LoadError(f"Неподдерживаемый формат файла: {filename} "
                        f"(можно: {', '.join(ACCEPTED_EXTS)})")
    df = _clean(df)
    if slot_cfg.get("fields"):
        df, mapped = fdict.map_columns(df, slot_cfg.get("fields"))
    elif slot_cfg.get("canonical"):
        df, mapped = fdict.canonicalize(df)
    else:
        mapped = []
    info.update({"rows": int(len(df)), "columns": int(len(df.columns)), "mapped": mapped})
    return df, info


def _read_zip(data: bytes, slot, cfg) -> tuple[pd.DataFrame, dict]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise LoadError("архив ZIP повреждён")
    members = [m for m in zf.infolist() if not m.is_dir()
               and os.path.splitext(m.filename)[1].lower() in (".xml",) + TABLE_EXTS
               and not os.path.basename(m.filename).startswith(("~", "."))]
    if sum(m.file_size for m in members) > MAX_ZIP_TOTAL:
        raise LoadError("архив слишком большой после распаковки (больше 300 МБ)")
    frames, names = [], []
    for m in members:
        content = zf.read(m)
        name = os.path.basename(m.filename)
        sub, _ = read_table(content, name, slot, cfg)
        if "source_file" not in sub.columns:
            sub.insert(0, "source_file", name)
        frames.append(sub)
        names.append(name)
    if not frames:
        raise LoadError("в архиве нет файлов XML / Excel / CSV")
    return pd.concat(frames, ignore_index=True, sort=False), {"format": "zip", "members": names,
                                                               "declarations": len(names)}


def read_upload(fileobj, filename: str, slot: str | None = None, cfg: dict | None = None) -> pd.DataFrame:
    """Совместимость: file-like -> DataFrame."""
    data = fileobj.read() if hasattr(fileobj, "read") else bytes(fileobj)
    df, _ = read_table(data, filename, slot, cfg)
    return df


def read_many(files: list[tuple[bytes, str]], slot: str, cfg: dict) -> tuple[pd.DataFrame, dict]:
    """Несколько файлов в один слот (например, 20 ДТ в XML) -> одна таблица."""
    frames, infos = [], []
    for data, name in files:
        df, info = read_table(data, name, slot, cfg)
        if len(files) > 1 and "source_file" not in df.columns:
            df.insert(0, "source_file", name)
        frames.append(df)
        infos.append(info)
    if not frames:
        raise LoadError("нет файлов")
    df = frames[0] if len(frames) == 1 else pd.concat(frames, ignore_index=True, sort=False)
    mapped = []
    for i in infos:
        for m in i.get("mapped") or []:
            if m not in mapped:
                mapped.append(m)
    return df, {"files": [i.get("file") for i in infos], "parts": infos, "rows": int(len(df)),
                "columns": int(len(df.columns)), "mapped": mapped,
                "warnings": [i["warning"] for i in infos if i.get("warning")]}


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
            data = fileobj.read() if hasattr(fileobj, "read") else fileobj
            df, info = read_table(data, filename, slot, cfg)
            sources[slot] = Source(slot=slot, name=filename, df=df, info=info)
            continue
        builtin = scfg.get("builtin")
        if builtin:
            path = os.path.join(PROJECT_ROOT, builtin)
            if os.path.exists(path):
                with open(path, "rb") as fh:
                    df, info = read_table(fh.read(), os.path.basename(path), slot, cfg)
                sources[slot] = Source(slot=slot, name=f"[встроенный] {builtin}", df=df, info=info)
    return sources
