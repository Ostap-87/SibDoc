from __future__ import annotations
# -*- coding: utf-8 -*-
"""Проверка 1: цены CD vs SAP по контейнерам (handbook, часть I)."""
import pandas as pd
from app.models import CheckOutput, Status
from app.normalizers import norm_container, norm_price, norm_text, scale_cd_price
from app.checks import register


def run(sources: dict, rules: dict, cfg: dict) -> CheckOutput:
    wide = sources.get("wide_table")
    purchase = sources.get("purchase")
    if wide is None or purchase is None:
        return CheckOutput("price", "1. Price", pd.DataFrame([{
            "check": "1. Price", "status": Status.SKIPPED.value,
            "detail": "Нужны wide table и Purchase."}]))

    tol = rules.get("tolerance_abs", 0.01)
    thr = rules.get("per_kg_threshold", 100)
    excl = rules.get("bp_sea_contract_patterns") or []
    escalation = rules.get("escalation", "SISH finance / export teams / CN OL / SVS team")

    w = wide.df.copy()
    w["ctn"] = w["container_num"].map(norm_container)
    w["cd_price"] = w["products_price"].map(norm_price).map(lambda v: scale_cd_price(v, thr))

    p = purchase.df.copy()
    p["ctn"] = p["Container number"].map(norm_container)
    p["sap_price"] = p["Price"].map(norm_price)
    p["sap_contract"] = (p["Main contract"].map(norm_text) + "-" +
                         p["Add contract"].map(norm_text)).str.strip("-")

    price_map: dict[str, list[tuple[float, str]]] = {}
    for ctn, grp in p.groupby("ctn"):
        if ctn:
            price_map[ctn] = list(zip(grp["sap_price"], grp["sap_contract"]))

    cd = (w.groupby("ctn", dropna=False)
            .agg(decl_num=("decl_num", "first"),
                 contract=("contract", "first"),
                 cd_price=("cd_price", "first"))
            .reset_index())
    cd = cd[cd["ctn"] != ""]

    rows = []
    for _, r in cd.iterrows():
        ctn = r["ctn"]
        sap_lines = price_map.get(ctn, [])
        rec = {"check": "1. Price", "container": ctn, "decl_num": r["decl_num"],
               "contract": r["contract"], "cd_price": r["cd_price"],
               "sap_price": sap_lines[0][0] if sap_lines else None,
               "sap_contract": sap_lines[0][1] if sap_lines else ""}

        if any(pat and pat in norm_text(r["contract"]) for pat in excl):
            rec.update(status=Status.SKIPPED.value,
                       detail="Схема BP SEA — исключена из месячной проверки (цикл 6 мес.)")
        elif not sap_lines:
            rec.update(status=Status.ISSUE.value,
                       detail="Нет цены SAP по контейнеру в отчёте Purchase")
        else:
            match = next((pr for pr, _ in sap_lines
                          if pd.notna(pr) and pd.notna(r["cd_price"])
                          and abs(pr - r["cd_price"]) <= tol), None)
            if match is not None:
                rec.update(status=Status.OK.value,
                           detail=("цена совпадает" if len(sap_lines) == 1 else
                                   f"цена совпала со 2-й строкой Purchase — фейковый «no» "
                                   f"(повторный контейнер, строк: {len(sap_lines)})"))
            else:
                rec.update(status=Status.ISSUE.value,
                           detail=f"РАСХОЖДЕНИЕ ЦЕНЫ: CD={r['cd_price']} vs SAP="
                                  f"{[pr for pr, _ in sap_lines]} -> эскалация: {escalation}")
        rows.append(rec)

    df = pd.DataFrame(rows)
    if not df.empty:
        df["_o"] = df["status"].map({Status.ISSUE.value: 0, Status.REVIEW.value: 1,
                                     Status.SKIPPED.value: 2, Status.OK.value: 3})
        df = df.sort_values("_o").drop(columns="_o")
    return CheckOutput("price", "1. Price", df)


register("price", "1. Price", run)
