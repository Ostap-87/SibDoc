# -*- coding: utf-8 -*-
"""SibDoc 3: разборщики веток «пакет ВЭД» и «коннекторы / банк» в пакетной загрузке.

Пакетная загрузка (app/intake) определяет тип документа и извлекает поля общим разбором.
Здесь к ней подключаются (register_extractor) настоящие разборщики, которые ретро-режим
использует для своих слотов, — чтобы документ пакета читался так же, как файл в слоте:

  invoice / packing / transport / conformity  -> app/services/package_docs.py
      таблицы (Excel, CSV, Word, PDF с таблицей): шапка над таблицей, «Итого», тонны -> кг,
      вид транспортного документа / сертификата; текст и сканы — паттерны
      config/scan_fields.yaml → package (как в слоте ВЭД-пакета);
  bank      -> app/services/bank_statements.py: 1CClientBankExchange, camt.053/052/054, MT940,
      CSV / Excel интернет-банков КНР и РФ (строка = платёж);
  contract  -> реестр договоров (1С EnterpriseData, Excel / CSV со столбцами «договор» + дата /
      сумма / контрагент) — как слот contract_register; текст договора — общий разбор;
  system_export -> 1С EnterpriseData: поступление товаров (как слот purchase).

Каждый разборщик дополняет общий разбор (поля, которые нашёл только он, остаются), а не
заменяет его. Если разборщик не подходит к документу (нет таблицы, не тот формат), он
возвращает None — работает общий разбор (app/intake/extract.py → extract()).
"""
from __future__ import annotations

import csv
import io
import math
import os

from app.intake import extract as ex

VED_KINDS = ("invoice", "packing", "transport", "conformity")
REGISTER_HINTS = {"contract_date", "contract_amount", "counterparty", "counterparty_inn", "unk"}


# ------------------------------------------------------------------ общее
def _sources_cfg() -> dict:
    from app.config_loader import load_config
    try:
        return load_config() or {}
    except Exception:  # noqa: BLE001 — без конфига разборщик не нужен
        return {}


def _slot_cfg(cfg: dict, slot: str) -> dict:
    return ((cfg.get("sources") or {}).get(slot) or {})


def _blank(v) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    return str(v).strip() in ("", "nan", "NaT", "None")


def _base_name(ctx: ex.DocContext) -> str:
    return os.path.basename(str(ctx.file or "").replace("\\", "/")) or "document"


def _kind_label(kind: str) -> str:
    from app.services import package_docs as pd_mod
    lab = pd_mod.KIND_LABELS.get(kind, kind)
    return lab[0] if isinstance(lab, (tuple, list)) else str(lab)


def _whole(ctx: ex.DocContext) -> bool:
    """Документ — весь файл (не часть многодокументного PDF / книги): можно читать исходные байты."""
    return bool(ctx.data) and not ctx.split


def _table_name(ctx: ex.DocContext) -> str:
    """Имя для чтения байтов: расширение по формату (часть ZIP могла прийти без расширения)."""
    name = _base_name(ctx)
    ext = {"excel": (".xlsx", ".xlsm", ".xls"), "csv": (".csv", ".tsv", ".txt"), "xml": (".xml",),
           "txt": (".txt", ".sta", ".940", ".mt940")}.get(ctx.fmt)
    if ext and not name.lower().endswith(ext):
        name += ext[0]
    return name


def _grid_csv(units: list) -> bytes:
    """Таблицы документа (листы Excel / CSV / таблицы Word) -> один CSV (только ЭТОГО документа,
    если файл делился на части)."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    for u in units:
        for grid in u.tables or []:
            for row in grid:
                w.writerow(["" if _blank(c) else str(c) for c in row])
    return buf.getvalue().encode("utf-8")


def _cell_key(v) -> str:
    return "".join(str(v).split()).lower().rstrip("0").rstrip(".") if not _blank(v) else ""


def _grid_layout(grid: list | None, info: dict) -> tuple[dict, dict]:
    """По таблице документа: {колонка таблицы -> буква} и {поле шапки / итога -> (строка, буква)}."""
    letters, cells = {}, {}
    h = int(info.get("header_row") or 0)
    if not grid or not h or h > len(grid):
        return letters, cells
    head = [str(c or "").strip() for c in grid[h - 1]]
    src = {m.get("field"): m.get("from") for m in info.get("mapped") or []}
    for i, name in enumerate(head):
        if name:
            letters.setdefault(name, ex._col_letter(i))
    for f, name in src.items():
        if name in letters:
            letters[f] = letters[name]
    return letters, cells


def _find_cell(grid: list | None, value) -> tuple[int | None, str | None]:
    """Где в таблице стоит значение поля шапки («Invoice No | INV-7» над таблицей, строка «Итого»)."""
    key = _cell_key(value)
    if not grid or not key:
        return None, None
    for r, row in enumerate(grid, start=1):
        for c, v in enumerate(row):
            if _cell_key(v) == key or (len(key) > 3 and key in _cell_key(v)):
                return r, ex._col_letter(c)
    return None, None


def _frame_items(ctx: ex.DocContext, col: ex.Collector, df, unit, method: str, how: str,
                 conf: float = 1.0, first_row: int | None = None, prefix: str = "p", slot_fields=None,
                 grid: list | None = None, info: dict | None = None) -> int:
    """Таблица (строка = позиция / платёж / договор) -> товарные строки документа.
    Колонки слота с «системными» названиями (Price, Container number …) — в поля единого словаря.
    grid + info (чтение package_docs): у значения — строка и буква колонки; поля над таблицей и
    из строки «Итого» — в шапку документа со своей ячейкой."""
    from app import fields as fdict
    info = info or {}
    letters, _ = _grid_layout(grid, info)
    block = set(info.get("header_block") or []) | set(info.get("totals") or [])
    cols = [c for c in df.columns if c not in ("source_file",)]
    known = fdict.dictionary()
    ren = {}
    for c in cols:
        f = fdict.field_of_column(str(c), slot_fields) if slot_fields else None
        if not f and str(c) not in known:
            f, _ = ex.field_for_label(str(c))        # подписи пакетной загрузки («Цена за т» -> цена)
        if f and f != c:
            ren[c] = f
            if c in letters:
                letters.setdefault(f, letters[c])
    if ren:
        df = df.rename(columns=ren)
        df = df.loc[:, ~df.columns.duplicated()]
        cols = [c for c in df.columns if c not in ("source_file",)]
    done_block: set = set()
    n = 0
    for i, (_, r) in enumerate(df.iterrows(), start=1):
        vals = {c: r.get(c) for c in cols if not _blank(r.get(c))}
        if not vals:
            continue
        key = f"{prefix}{i}"
        row_no = (first_row + i) if first_row else None
        item_no = vals.get("item_no") or str(i)
        for c, v in vals.items():
            if hasattr(v, "isoformat"):
                v = v.isoformat()[:10]
            if c in block and c not in letters:
                if c not in done_block:
                    br, bc = _find_cell(grid, v)
                    col.add(str(c), v, conf, ex._src(ctx, unit, method, row=br, col=bc),
                            how=how + (" (над таблицей)" if c in (info.get("header_block") or []) else " (итого)"))
                    done_block.add(c)
                continue
            col.add(str(c), v, conf, ex._src(ctx, unit, method, row=row_no, col=letters.get(c)), how=how,
                    item=key, item_no=str(item_no))
        n += 1
    return n


def _merge_generic(ctx: ex.DocContext, col: ex.Collector, keep_items: bool, fields: bool = True) -> dict:
    """Дополнить поля разборщика полями общего разбора (подписи «поле: значение», контейнеры…).
    Поле, которое разборщик уже нашёл — в шапке или в строках, — общий разбор не меняет."""
    base = ex.generic(ctx)
    ex._aggregate(col)                                   # общие для всех строк поля — в шапку
    in_items = {f for it in col.items.values() for f in it["fields"]}
    for f, fv in (base.get("fields") or {}).items() if fields else ():
        if f == "container" and f in col.header:
            col.put(col.header, f, fv)
        elif f not in col.header and f not in in_items:
            col.header[f] = fv
    if not col.items and keep_items:
        for k, it in enumerate(base.get("items") or [], start=1):
            col.items[f"g{k}"] = it
    col.kv = list(base.get("kv") or [])
    return base


# ------------------------------------------------------------------ пакет ВЭД
def ved_document(ctx: ex.DocContext) -> dict | None:
    """Инвойс, упаковочный лист, транспортный документ, сертификат — как в слотах пакета ВЭД."""
    from app.services import package_docs as pd_mod
    kind = ctx.type
    if kind not in VED_KINDS:
        return None
    cfg = _sources_cfg()
    slot_cfg = dict(_slot_cfg(cfg, kind)) or {"package": kind, "canonical": True}
    slot_cfg.setdefault("package", kind)
    col = ex.Collector(kind)
    notes: list[str] = []
    used = ""
    # 1а) весь файл Excel / CSV — тем же чтением, что и слот ретро-режима (лучший лист, шапка, «Итого»)
    if _whole(ctx) and ctx.fmt in ("excel", "csv"):
        try:
            df, info = pd_mod.read_document(ctx.data, _table_name(ctx), slot_cfg)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"файл не прочитан как документ пакета: {exc}")
            df, info = None, {}
        if df is not None and len(df.columns) > 1:
            unit = next((u for u in ctx.units if info.get("sheet") and u.sheet == info.get("sheet")),
                        ctx.units[0] if ctx.units else None)
            grid = (unit.tables or [None])[0] if unit is not None else None
            n = _frame_items(ctx, col, df, unit, "table", f"ВЭД-пакет: {_kind_label(kind)}",
                             first_row=info.get("header_row"), grid=grid, info=info)
            used = f"таблица ({n} строк)"
            notes += [f"{f}: тонны переведены в кг" for f in info.get("converted_t_to_kg") or []]
    # 1б) таблица части файла / Word / PDF: лист или таблица, больше всего похожие на документ этого вида
    best = None
    if not used:
        for unit in ctx.units:
            for grid in unit.tables or []:
                if not grid or len(grid) < 2:
                    continue
                try:
                    sc = pd_mod._sheet_score(unit.sheet or "", grid, kind)
                except Exception:  # noqa: BLE001
                    continue
                if best is None or sc > best[0]:
                    best = (sc, unit, grid)
    if best is not None and best[0] > 0:
        _, unit, grid = best
        try:
            df, info = pd_mod.grid_to_frame([["" if _blank(c) else c for c in row] for row in grid], kind,
                                            slot_cfg, ctx.file)
        except Exception as exc:  # noqa: BLE001 — таблица не разобралась: дальше текст и общий разбор
            notes.append(f"таблица не разобрана: {exc}")
            df, info = None, {}
        if df is not None and len(df.columns) > 1:
            conf = 1.0 if unit.method != "ocr" else 0.8
            n = _frame_items(ctx, col, df, unit, "table", f"ВЭД-пакет: {_kind_label(kind)}",
                             conf=conf, first_row=info.get("header_row"), grid=grid, info=info)
            used = f"таблица ({n} строк)"
            for f in info.get("converted_t_to_kg") or []:
                notes.append(f"{f}: тонны переведены в кг")
    # 2) текст (PDF, скан, письмо, Word без таблицы): паттерны config/scan_fields.yaml → package
    text_units = [u for u in ctx.units if u.lines and u.kind not in ("sheet", "xml")] \
        if ctx.fmt not in ("excel", "csv") else []
    if text_units:
        text = "\n".join(u.text for u in text_units)
        try:
            parsed = pd_mod.parse_text_fields(text, kind)
        except Exception as exc:  # noqa: BLE001
            parsed = {}
            notes.append(f"текст не разобран: {exc}")
        found = 0
        for f, rec in parsed.items():
            if not rec.get("value") or rec.get("status") == "missing":
                continue
            unit, ln_no, ln = text_units[0], None, None
            for u in text_units:
                ln_no, ln = ex._find_line(u, str(rec["value"]).split("; ")[0])
                if ln is not None:
                    unit = u
                    break
            ocr = unit.method == "ocr"
            conf = (ln.conf if (ln is not None and ocr) else (unit.ocr_conf or 1.0) if ocr else 1.0)
            conf *= 0.9 * (1.0 if rec.get("status") == "found" else 0.7)
            col.add(f, rec["value"], conf, ex._src(ctx, unit, "pattern", line=ln_no,
                                                   ocr_conf=conf if ocr else None),
                    how="ВЭД-пакет: шаблон config/scan_fields.yaml → package")
            found += 1
        if found:
            used = (used + "; " if used else "") + f"текст ({found} полей)"
    if not used:
        return None                          # разборщику не за что зацепиться — общий разбор
    _merge_generic(ctx, col, keep_items=True)
    res = ex.finalize(ctx, col, f"ВЭД-пакет: {_kind_label(kind)} (package_docs: {used})")
    res["warnings"] = notes + res["warnings"]
    return res


# ------------------------------------------------------------------ выписки банка
def _bank_frame(ctx: ex.DocContext, cfg: dict):
    """-> (таблица платежей, формат) или (None, ""). Весь файл — тем же чтением, что слот «Банк: выписки»
    (формат по содержимому: 1С, camt, MT940, CSV / Excel банка); часть файла — по её таблицам."""
    from app.services import loader
    if _whole(ctx) and ctx.fmt in ("txt", "xml", "csv", "excel"):
        df, info = loader.read_table(ctx.data, _table_name(ctx), "bank", cfg)
        return df, str(info.get("format") or "")
    if any(u.tables for u in ctx.units):
        df, info = loader.read_table(_grid_csv(ctx.units), "statement.csv", "bank", cfg)
        return df, "таблица банка"
    return None, ""


def bank_document(ctx: ex.DocContext) -> dict | None:
    """Выписка банка: строка = платёж (дата, сумма, валюта, приход / расход, контрагент, назначение)."""
    cfg = _sources_cfg()
    try:
        df, fmt = _bank_frame(ctx, cfg)
    except Exception as exc:  # noqa: BLE001 — не выписка известного формата: общий разбор
        ctx.warnings.append(f"выписка: {exc}")
        return None
    if df is None or df.empty or "amount" not in df.columns:
        return None
    col = ex.Collector("bank")
    unit = ctx.units[0] if ctx.units else None
    n = _frame_items(ctx, col, df, unit, "bank_statement", f"выписка банка ({fmt})" if fmt else "выписка банка",
                     prefix="b")
    _merge_generic(ctx, col, keep_items=False, fields=False)
    return ex.finalize(ctx, col, f"Выписка банка: {fmt or 'таблица'}, платежей {n} (bank_statements)")


# ------------------------------------------------------------------ договоры (реестр) и 1С EnterpriseData
def _enterprise_parts(ctx: ex.DocContext) -> dict:
    from app.services import enterprise_data
    if ctx.fmt != "xml" or not ctx.data or not enterprise_data.is_enterprise_data(ctx.data):
        return {}
    parts, _ = enterprise_data.parse(ctx.data, _base_name(ctx))
    return parts


def contract_document(ctx: ex.DocContext) -> dict | None:
    """Реестр договоров (1С EnterpriseData или таблица «договор + дата / сумма / контрагент»)."""
    from app.services import loader
    cfg = _sources_cfg()
    df, how = None, ""
    try:
        parts = _enterprise_parts(ctx)
        if parts.get("contract_register") is not None:
            df, how = parts["contract_register"], "1С EnterpriseData"
        elif _whole(ctx) and ctx.fmt in ("excel", "csv"):
            df, _ = loader.read_table(ctx.data, _table_name(ctx), "contract_register", cfg)
            how = "таблица договоров"
        elif any(u.tables for u in ctx.units) and ctx.fmt in ("excel", "csv", "docx", "html"):
            df, _ = loader.read_table(_grid_csv(ctx.units), "contracts.csv", "contract_register", cfg)
            how = "таблица договоров"
    except Exception as exc:  # noqa: BLE001
        ctx.warnings.append(f"реестр договоров: {exc}")
        return None
    if df is None or df.empty or "contract" not in df.columns or not (REGISTER_HINTS & set(df.columns)):
        return None                          # текст договора — общий разбор
    col = ex.Collector("contract")
    n = _frame_items(ctx, col, df, ctx.units[0] if ctx.units else None, "table", f"реестр договоров ({how})",
                     prefix="c")
    _merge_generic(ctx, col, keep_items=False)
    return ex.finalize(ctx, col, f"Реестр договоров: {how}, договоров {n}")


def system_export_document(ctx: ex.DocContext) -> dict | None:
    """1С EnterpriseData: поступление товаров -> строки выгрузки (как слот purchase)."""
    try:
        parts = _enterprise_parts(ctx)
    except Exception as exc:  # noqa: BLE001
        ctx.warnings.append(f"1С EnterpriseData: {exc}")
        return None
    df = parts.get("purchase")
    if df is None or df.empty:
        return None
    col = ex.Collector("system_export")
    n = _frame_items(ctx, col, df, ctx.units[0] if ctx.units else None, "xml", "1С EnterpriseData", prefix="e",
                     slot_fields=_slot_cfg(_sources_cfg(), "purchase").get("fields"))
    _merge_generic(ctx, col, keep_items=False)
    res = ex.finalize(ctx, col, f"1С EnterpriseData: строк {n}")
    others = [s for s in parts if s != "purchase"]
    if others:
        res["warnings"].append(f"в файле есть и другие данные 1С: {', '.join(others)} — «В сверку» разложит их по слотам")
    return res


for _kind in VED_KINDS:
    ex.register_extractor(_kind, ved_document, name="ВЭД-пакет (package_docs)", priority=60)
ex.register_extractor("bank", bank_document, name="выписка банка (bank_statements)", priority=60)
ex.register_extractor("contract", contract_document, name="реестр договоров (contract_register)", priority=60)
ex.register_extractor("system_export", system_export_document, name="1С EnterpriseData", priority=60)
