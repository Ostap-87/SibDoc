from __future__ import annotations
# -*- coding: utf-8 -*-
"""Сборка «wide table» из документов пакета — чтобы проверки 0–5 работали без выгрузки FineBI.

Штатный путь: аналитик выгружает wide table из FineBI и кладёт её в слот wide_table.
Но у брокера этой выгрузки может не быть — есть только декларации. Тогда wide table
собирается из них: ДТ РФ и 报关单 КНР, по строке на товар.

Колонки — те, которые ждут проверки (config/system.yaml → sources.wide_table):
    decl_num, container_num, contract, incoterms, shipreport_incoterms, extra_costs_value,
    product_code, product_name, material, products_price, products_net_weight,
    storage_location, decl_issue_status
Плюс несколько «родных» полей деклараций (hs_code, net_weight, unit_price, currency, item_no, qty),
чтобы проверки могли ими пользоваться, если решат считать иначе.

Что честно: собранная таблица — это данные деклараций, а не данные складского учёта FineBI.
Проверки, которые сравнивают CD с SAP, увидят декларацию и SAP; проверки, которым нужны
складские остатки или статусы выпуска, честно уйдут в REVIEW/SKIPPED — как и раньше.
"""
import pandas as pd

DECL_TYPES = ("ru_dt", "cn_dec")

COLUMNS = [
    "decl_num", "container_num", "contract", "incoterms", "shipreport_incoterms",
    "extra_costs_value", "product_code", "product_name", "material",
    "products_price", "products_net_weight", "storage_location", "decl_issue_status",
    # «родные» поля деклараций
    "decl_cn", "item_no", "hs_code", "net_weight", "unit_price", "currency", "qty",
]


def _val(field) -> str:
    """Поле документа может быть строкой или {"value": ...} — приводим к строке."""
    if isinstance(field, dict):
        field = field.get("value")
    return "" if field is None else str(field).strip()


def has_declarations(docs: list) -> bool:
    return any(d.get("type") in DECL_TYPES and d.get("status") not in ("ошибка", "не распознан")
               for d in docs)


def build_rows(docs: list) -> list[dict]:
    """Документы пакета -> строки wide table (по строке на товар декларации)."""
    rows: list[dict] = []
    for d in docs:
        if d.get("type") not in DECL_TYPES:
            continue
        if d.get("status") in ("ошибка", "не распознан"):
            continue
        head = {k: _val(v) for k, v in (d.get("fields") or {}).items()}
        decl_num = head.get("decl_ru") or head.get("decl_cn") or _val(d.get("doc_number"))
        items = d.get("items") or []
        if not items:                       # декларация без строк товара — одна строка по шапке
            items = [{"no": "", "fields": {}}]
        for it in items:
            f = {k: _val(v) for k, v in (it.get("fields") or {}).items()}
            rows.append({
                "decl_num": decl_num,
                "container_num": f.get("container") or head.get("container", ""),
                "contract": head.get("contract", ""),
                "incoterms": head.get("incoterms", ""),
                "shipreport_incoterms": head.get("incoterms", ""),
                "extra_costs_value": "",
                "product_code": f.get("hs_code", ""),
                "product_name": f.get("product_name", ""),
                "material": f.get("model") or f.get("material") or f.get("product_name", ""),
                "products_price": f.get("unit_price", ""),
                "products_net_weight": f.get("net_weight", ""),
                "storage_location": "",
                "decl_issue_status": "OK",
                "decl_cn": head.get("decl_cn", ""),
                "item_no": it.get("no", ""),
                "hs_code": f.get("hs_code", ""),
                "net_weight": f.get("net_weight", ""),
                "unit_price": f.get("unit_price", ""),
                "currency": head.get("currency", ""),
                "qty": f.get("qty", ""),
            })
    return rows


def to_dataframe(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    return df[[c for c in COLUMNS if c in df.columns]] if not df.empty else pd.DataFrame(columns=COLUMNS)
