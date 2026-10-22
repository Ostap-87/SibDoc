from __future__ import annotations
# -*- coding: utf-8 -*-
"""Общие помощники проверок: колонки по словарю, сортировка, строки SAP, контракты, курсы."""
import re

import numpy as np
import pandas as pd

from app.models import CheckOutput, Status
from app.normalizers import (canon_contract, is_blank, norm_container, norm_date, norm_decl,
                             norm_number, norm_text, norm_unit, split_contracts)

ORDER = {Status.ISSUE.value: 0, Status.REVIEW.value: 1, Status.SKIPPED.value: 2, Status.OK.value: 3}
WORST = [Status.ISSUE.value, Status.REVIEW.value, Status.OK.value, Status.SKIPPED.value]


def skipped(check_id: str, title: str, detail: str, **meta) -> CheckOutput:
    return CheckOutput(check_id, title, pd.DataFrame([{
        "check": title, "status": Status.SKIPPED.value, "detail": detail}]), meta=meta)


def sort_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "status" not in df.columns:
        return df
    df = df.copy()
    df["_o"] = df["status"].map(ORDER).fillna(9)
    return df.sort_values("_o", kind="stable").drop(columns="_o").reset_index(drop=True)


def worst(statuses) -> str:
    sts = [s for s in statuses if s]
    for s in WORST:
        if s in sts:
            return s
    return Status.SKIPPED.value


class Cols:
    """Имя колонки поля словаря в конкретном слоте: Cols(cfg, 'purchase')('container') -> 'Container number'."""

    def __init__(self, cfg: dict, slot: str, df: pd.DataFrame | None = None):
        scfg = (cfg.get("sources") or {}).get(slot) or {}
        self.map = dict(scfg.get("fields") or {})
        self.units = dict(scfg.get("units") or {})
        self.df = df

    def __call__(self, field: str) -> str | None:
        name = self.map.get(field, field)
        if self.df is not None and name not in self.df.columns:
            return None
        return name

    def get(self, row, field: str, default=None):
        name = self(field)
        if name is None:
            return default
        v = row.get(name)
        return default if is_blank(v) else v


def split_containers(v) -> list[str]:
    """«ABCD1234567; EFGH7654321» -> [...] (нормализованные, без пустых)."""
    if is_blank(v):
        return []
    out = []
    for part in re.split(r"[;,，、\n]+|\s{2,}", str(v)):
        c = norm_container(part)
        if c and c not in out:
            out.append(c)
    return out


def sap_lines(sources: dict, cfg: dict) -> pd.DataFrame:
    """Строки SAP в едином виде: container, contract, contract_main, price, currency, net_t,
    decl_ru, material, date. Из Purchase; если его нет — из SAP-колонок wide table."""
    p = sources.get("purchase")
    if p is not None and not p.df.empty:
        C = Cols(cfg, "purchase", p.df)
        df, unit_w = p.df, C.units.get("net_weight", "t")
    else:
        w = sources.get("wide_table")
        if w is None:
            return pd.DataFrame()
        C = Cols(cfg, "wide_table", w.df)
        df, unit_w = w.df, None
    rows = []
    for _, r in df.iterrows():
        ctn = norm_container(C.get(r, "container"))
        if not ctn:
            continue
        main = norm_text(C.get(r, "main_contract", ""))
        add = norm_text(C.get(r, "add_contract", ""))
        cur_field = "currency" if p is not None else "sap_currency"
        net = norm_number(C.get(r, "net_weight")) if unit_w else np.nan
        rows.append({
            "container": ctn,
            "contract": "-".join(x for x in (main, add) if x),
            "contract_main": main,
            "price": norm_number(C.get(r, "sap_price")),
            "currency": norm_currency_safe(C.get(r, cur_field, "")),
            "net_t": net * (1 if unit_w == "t" else 0.001) if not np.isnan(net) else np.nan,
            "decl_ru": norm_decl(C.get(r, "decl_ru", "")),
            "material": norm_text(C.get(r, "material", "")).split(".")[0],
            "date": norm_date(C.get(r, "shipment_date", "")),
        })
    out = pd.DataFrame(rows)
    if p is None and not out.empty:
        out = out.drop_duplicates(["container", "contract", "price", "decl_ru"])
    return out


def contract_match(cd_contract, sap_contract: str, ignore_chars: str | None = None) -> str:
    """'full' — совпал контракт целиком (с учётом склейки); 'main' — совпал только основной
    контракт (доп. соглашение не указано в CD); '' — нет."""
    sap_c = canon_contract(sap_contract, ignore_chars)
    if not sap_c:
        return ""
    parts = split_contracts(cd_contract) or [norm_text(cd_contract)]
    keys = [canon_contract(x, ignore_chars) for x in parts if norm_text(x)]
    if sap_c in keys:
        return "full"
    main = canon_contract(sap_contract.split("-")[0] if "-" in sap_contract else sap_contract, ignore_chars)
    if main and main in keys:
        return "main"
    return ""


def norm_currency_safe(v) -> str:
    """Код валюты (через модуль курсов, если он есть)."""
    try:
        from app.rates import norm_currency
        return norm_currency(v) if not is_blank(v) else ""
    except Exception:  # noqa: BLE001 — модуль курсов необязателен для простых проверок
        s = norm_text(v).upper()
        return {"人民币": "CNY", "美元": "USD", "欧元": "EUR", "卢布": "RUB", "RMB": "CNY"}.get(s, s)


def fx_convert(amount: float, from_ccy: str, to_ccy: str, on: str, side: str = "cn"):
    """-> (значение, описание курса) или (NaN, причина)."""
    if not from_ccy or not to_ccy or from_ccy == to_ccy:
        return amount, ""
    try:
        from app.rates import convert
        val, info = convert(amount, from_ccy, to_ccy, on or pd.Timestamp.today().strftime("%Y-%m-%d"), side=side)
        d = info.as_dict() if hasattr(info, "as_dict") else dict(info)
        desc = (f"курс {d.get('base')}/{d.get('quote')} = {d.get('value'):.6g} "
                f"({d.get('source')}, {d.get('effective_date') or d.get('date')}"
                f"{', ' + d['note'] if d.get('note') else ''})")
        return val, desc
    except Exception as exc:  # noqa: BLE001
        return np.nan, f"нет курса {from_ccy}->{to_ccy} на {on or 'дату'}: {exc}"


def unit_of(row, C: Cols, field: str, rule_unit: str = "auto") -> str:
    if rule_unit and rule_unit != "auto":
        return norm_unit(rule_unit) or rule_unit
    u = norm_unit(C.get(row, "unit", ""))
    return u
