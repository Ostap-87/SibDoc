from __future__ import annotations
# -*- coding: utf-8 -*-
"""Проверка 1: цены CD vs SAP (handbook, часть I).

Одна строка результата = одна товарная строка декларации КНР в одном контейнере
(контейнер × декларация × № товара), поэтому ошибка во второй строке
контейнера не прячется за первой.

Как сравниваем:
  - цена CD приводится к цене за тонну по единице измерения из данных
    (кг/т); если единицы нет — запасное правило порога per_kg_threshold;
  - строки SAP по контейнеру: сначала те, где совпал контракт (с учётом
    «склеенных» контрактов и кириллицы/латиницы), затем — по правилу
    повторного контейнера (handbook 2.3.1) — остальные строки контейнера;
  - валюта сравнивается всегда; при разных валютах цена пересчитывается по
    официальному курсу на дату декларации (или ручному — см. «Курсы валют»).
"""
import numpy as np
import pandas as pd

from app.checks import register
from app.checks._common import (Cols, contract_match, fx_convert, norm_currency_safe, skipped,
                                sap_lines, sort_df)
from app.models import CheckOutput, Status
from app.normalizers import (nearly_equal, norm_container, norm_date, norm_number, norm_text,
                             norm_unit, price_per_ton)

TITLE = "1. Price"


def _fmt(v, nd=2) -> str:
    return "" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:,.{nd}f}".replace(",", " ")


def run(sources: dict, rules: dict, cfg: dict) -> CheckOutput:
    wide = sources.get("wide_table")
    if wide is None or sources.get("purchase") is None:
        return skipped("price", TITLE, "Нужны wide table и Purchase.")

    tol = float(rules.get("tolerance_abs", 0.01))
    tol_pct = float(rules.get("tolerance_pct", 0) or 0)
    thr = float(rules.get("per_kg_threshold", 100))
    unit_rule = str(rules.get("cd_price_unit", "auto") or "auto")
    scope = str(rules.get("match_scope", "contract_first"))
    fx_mode = str(rules.get("currency_mismatch", "convert"))
    fx_tol = float(rules.get("fx_tolerance_pct", 0.5))
    fx_side = str(rules.get("fx_side", "cn"))
    excl = [str(p) for p in (rules.get("bp_sea_contract_patterns") or []) if str(p).strip()]
    escalation = rules.get("escalation", "SISH finance / export teams / CN OL / SVS team")
    ignore = (load_contract_ignore(cfg))

    W = Cols(cfg, "wide_table", wide.df)
    sap = sap_lines(sources, cfg)
    by_ctn = {c: g for c, g in sap.groupby("container")} if not sap.empty else {}

    # товарные строки CD: контейнер × декларация × № товара (дубли от склейки с SAP схлопываем)
    w = wide.df.copy()
    w["_ctn"] = w[W("container")].map(norm_container) if W("container") else ""
    w = w[w["_ctn"] != ""]
    key = ["_ctn"] + [c for c in (W("decl_cn"), W("item_no"), W("hs_code")) if c]
    cd_lines = w.drop_duplicates(key)

    rows, seen = [], set()
    for _, r in cd_lines.iterrows():
        ctn = r["_ctn"]
        seen.add(ctn)
        contract = norm_text(W.get(r, "contract", ""))
        raw_price = norm_number(W.get(r, "unit_price"))
        unit = norm_unit(unit_rule) if unit_rule != "auto" else norm_unit(W.get(r, "unit", ""))
        cd_t, basis = price_per_ton(raw_price, unit, thr)
        cd_cur = norm_currency_safe(W.get(r, "currency", ""))
        on = norm_date(W.get(r, "decl_date", ""))
        rec = {"check": TITLE, "container": ctn, "decl_num": W.get(r, "decl_cn", ""),
               "item_no": W.get(r, "item_no", ""), "contract": contract,
               "cd_price": raw_price, "cd_unit": unit or ("кг (по порогу)" if basis == "threshold" else ""),
               "cd_price_per_t": cd_t, "cd_currency": cd_cur,
               "sap_price": None, "sap_currency": "", "sap_contract": "", "sap_lines": "", "fx_rate": ""}

        lines = by_ctn.get(ctn)
        if any(p in contract for p in excl):
            rec.update(status=Status.SKIPPED.value,
                       detail="Схема BP SEA — исключена из месячной проверки (цикл 6 мес.)")
            rows.append(rec)
            continue
        if lines is None or lines.empty:
            rec.update(status=Status.ISSUE.value, detail="Нет цены SAP по контейнеру в отчёте Purchase")
            rows.append(rec)
            continue
        if np.isnan(cd_t):
            rec.update(status=Status.REVIEW.value, detail="В декларации нет цены (пусто или не число)",
                       sap_lines=_lines_txt(lines))
            rows.append(rec)
            continue

        lines = lines.assign(_m=[contract_match(contract, c, ignore) for c in lines["contract"]])
        own = lines[lines["_m"] == "full"]
        main_only = lines[lines["_m"] == "main"]
        other = lines[lines["_m"] == ""]
        # строки своего контракта главнее: если они есть, «чужая» строка повторного
        # контейнера не может закрыть расхождение (any_line — прежнее поведение)
        groups = [("own", own), ("main", main_only)]
        if scope == "any_line" or (scope != "contract_only" and own.empty and main_only.empty):
            groups.append(("other", other))
        rec["sap_lines"] = _lines_txt(lines)

        found = None
        for kind, grp in groups:
            for _, s in grp.iterrows():
                sap_cur = s["currency"] or cd_cur
                cmp_val, rate_desc = cd_t, ""
                if cd_cur and sap_cur and cd_cur != sap_cur:
                    if fx_mode != "convert":
                        continue
                    cmp_val, rate_desc = fx_convert(cd_t, cd_cur, sap_cur, on, side=fx_side)
                    if np.isnan(cmp_val):
                        found = found or ("norate", kind, s, rate_desc)
                        continue
                    if nearly_equal(cmp_val, s["price"], tol, fx_tol):
                        found = ("fx", kind, s, rate_desc, cmp_val)
                        break
                elif nearly_equal(cmp_val, s["price"], tol, tol_pct):
                    found = ("exact", kind, s, "", cmp_val)
                    break
            if found and found[0] in ("exact", "fx"):
                break

        if found and found[0] in ("exact", "fx"):
            how, kind, s, rate_desc, val = found
            rec.update(sap_price=s["price"], sap_currency=s["currency"], sap_contract=s["contract"],
                       fx_rate=rate_desc)
            where = {"own": f"строка SAP того же контракта ({s['contract']})",
                     "main": f"строка SAP основного контракта ({s['contract']}; доп. соглашение в CD не указано)",
                     "other": (f"другая строка SAP по контейнеру ({s['contract']}) — повторный контейнер, "
                               f"правило 2.3.1; строк SAP: {len(lines)}")}[kind]
            if how == "exact":
                st = Status.OK.value if kind in ("own", "other") else Status.REVIEW.value
                rec.update(status=st, detail=f"цена совпадает: {where}")
            else:
                rec.update(status=Status.REVIEW.value,
                           detail=f"валюты разные (CD {cd_cur}, SAP {s['currency']}): после пересчёта "
                                  f"{_fmt(val)} {s['currency']}/т ≈ SAP {_fmt(s['price'])}; {rate_desc}; {where}")
        elif found and found[0] == "norate":
            _, kind, s, why = found
            rec.update(status=Status.REVIEW.value, sap_price=s["price"], sap_currency=s["currency"],
                       sap_contract=s["contract"],
                       detail=f"валюты разные (CD {cd_cur}, SAP {s['currency']}), сравнить нельзя: {why}")
        else:
            ref = own if not own.empty else (main_only if not main_only.empty else lines)
            s = ref.iloc[0]
            rec.update(sap_price=s["price"], sap_currency=s["currency"], sap_contract=s["contract"])
            cur_note = ""
            if cd_cur and s["currency"] and cd_cur != s["currency"]:
                conv, rate_desc = fx_convert(cd_t, cd_cur, s["currency"], on, side=fx_side)
                cur_note = (f" = {_fmt(conv)} {s['currency']}/т ({rate_desc})" if not np.isnan(conv)
                            else f" (валюта CD {cd_cur}, SAP {s['currency']}; {rate_desc})")
                rec["fx_rate"] = rate_desc
            rec.update(status=Status.ISSUE.value,
                       detail=(f"РАСХОЖДЕНИЕ ЦЕНЫ: CD {_fmt(cd_t)} {cd_cur}/т{cur_note} vs SAP "
                               f"{rec['sap_lines']} -> эскалация: {escalation}"))
        rows.append(rec)

    if rules.get("report_sap_only", True):
        for ctn, g in by_ctn.items():
            if ctn not in seen:
                rows.append({"check": TITLE, "container": ctn, "sap_lines": _lines_txt(g),
                             "status": Status.REVIEW.value,
                             "detail": "контейнер есть в SAP, но нет в декларациях КНР (wide table)"})

    df = pd.DataFrame(rows)
    return CheckOutput("price", TITLE, sort_df(df), meta={"cd_lines": len(cd_lines)})


def _lines_txt(lines: pd.DataFrame) -> str:
    return "; ".join(f"{_fmt(p)} {c or ''} ({k or 'без контракта'})".replace("  ", " ")
                     for p, c, k in zip(lines["price"], lines["currency"], lines["contract"]))


def load_contract_ignore(cfg: dict) -> str | None:
    """Символы, игнорируемые в контрактах (config/rules/contracts.yaml -> ignore_chars)."""
    from app.config_loader import load_rules
    rf = ((cfg.get("checks") or {}).get("contracts") or {}).get("rules", "contracts.yaml")
    v = load_rules(rf).get("ignore_chars")
    return None if v is None else str(v)


register("price", TITLE, run)
