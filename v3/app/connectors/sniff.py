# -*- coding: utf-8 -*-
"""Коннекторы/банк: слот файла по содержимому (для мостов export_dir).

Выгрузки сохраняют под любыми именами («export(3).xml», «kl_to_1c.txt», «导出.xlsx»),
поэтому, кроме slot_rules по имени, мост смотрит внутрь файла:
  * выписка банка: 1CClientBankExchange, camt.052/053/054, MT940 -> bank;
  * 1С EnterpriseData -> слоты по объектам (purchase, ru_dt, contract_register);
  * XML деклараций: ESADout_CU -> ru_dt, DecMessage -> cn_dec (config/xml_profiles.yaml);
  * Excel/CSV: заголовки по словарю полей — 海关编号 / 报关单号 -> cn_dec, «Номер ДТ» -> ru_dt,
    дата + сумма/дебет/кредит -> bank, «Номер накладной» / «Номер вагона» -> транспортные документы.
slots_of(name, data, allowed) -> список слотов (из allowed), обычно один.
"""
from __future__ import annotations

import io
import os

TRANSPORT_HEADERS = ("номер накладной", "накладная", "№ накладной", "номер отправки", "номер вагона",
                     "вагон", "станция отправления", "станция назначения", "код груза етснг", "运单号", "车号")


def _header_fields(cells) -> set[str]:
    from app import fields as fdict
    idx = fdict.synonyms_index()
    return {idx.get(fdict.header_key(c)) for c in cells if c is not None} - {None}


def _table_headers(name: str, data: bytes) -> list[list]:
    low = name.lower()
    rows: list[list] = []
    try:
        if low.endswith((".xlsx", ".xlsm", ".xls")):
            import pandas as pd
            book = pd.ExcelFile(io.BytesIO(data), engine="xlrd" if low.endswith(".xls") else "openpyxl")
            for sh in book.sheet_names[:5]:
                raw = book.parse(sh, header=None, nrows=15, dtype=object)
                rows += [[v for v in r if v is not None and str(v) != "nan"] for r in raw.values.tolist()]
        elif low.endswith((".csv", ".txt")):
            from app.connectors.fileutil import smart_decode
            text = smart_decode(data[:20000])
            for ln in text.splitlines()[:15]:
                for d in (",", ";", "\t", "|"):
                    if d in ln:
                        rows.append([c.strip() for c in ln.split(d)])
                        break
    except Exception:  # noqa: BLE001 — нечитаемый файл просто остаётся без слота
        return []
    return rows


def slots_of(name: str, data: bytes, allowed=None) -> list[str]:
    allowed = list(allowed or [])
    ok = (lambda s: not allowed or s in allowed)
    found: list[str] = []
    low = name.lower()
    try:
        from app.services import bank_statements
        if bank_statements.detect(data, name) and ok("bank"):
            return ["bank"]
    except Exception:  # noqa: BLE001
        pass
    head = data[:300].lstrip(b"\xef\xbb\xbf \r\n\t")
    if low.endswith(".xml") or head.startswith(b"<"):
        try:
            from app.services import enterprise_data
            if enterprise_data.is_enterprise_data(data):
                parts, _ = enterprise_data.parse(data, name)
                return [s for s in parts if ok(s)]
        except Exception:  # noqa: BLE001
            return []
        try:
            from app.services import xml_decl
            prof = xml_decl.detect_profile(xml_decl.parse_root(data))
            slot = (xml_decl.load_profiles().get(prof) or {}).get("slot") if prof else None
            return [slot] if slot and ok(slot) else []
        except Exception:  # noqa: BLE001
            return []
    rows = _table_headers(name, data)
    best: tuple[int, str] | None = None
    for cells in rows:
        f = _header_fields(cells)
        keys = {str(c).strip().lower() for c in cells}
        cands = []
        sap_like = bool({"material", "main_contract", "add_contract", "sap_price", "sap_incoterms",
                         "storage_location", "extra_costs"} & f)            # wide table / Purchase (SAP, FineBI)
        if "decl_cn" in f and not sap_like:
            cands.append((3, "cn_dec"))
        if "decl_ru" in f and not sap_like:
            cands.append((2, "ru_dt"))
        if "pay_date" in f and ({"amount", "debit_amount", "credit_amount"} & f):
            cands.append((3, "bank"))
        if "contract" in f and ({"contract_date", "contract_amount"} & f):
            cands.append((2, "contract_register"))
        if sum(1 for k in keys if k in TRANSPORT_HEADERS) >= 2:
            cands.append((2, "transport"))
        for sc, slot in cands:
            if ok(slot) and (best is None or sc > best[0]):
                best = (sc, slot)
    if best:
        found.append(best[1])
    return found


def describe(path: str, allowed=None) -> list[str]:
    with open(path, "rb") as fh:
        return slots_of(os.path.basename(path), fh.read(), allowed)
