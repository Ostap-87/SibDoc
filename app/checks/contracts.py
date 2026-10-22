from __future__ import annotations
# -*- coding: utf-8 -*-
"""Проверка 5: контракт CD vs SAP (main + add contract) по контейнерам.

Сравнение каноническое: без пробелов/дефисов, верхний регистр.
Контракт в wide table может быть «склеенным» (SISH.12235-SISH.12236),
а в Purchase — отдельными строками; совпадение с ЛЮБОЙ строкой = OK.
"""
import pandas as pd
from app.models import CheckOutput, Status
from app.normalizers import canon_contract, norm_container, norm_text
from app.checks import register


def run(sources: dict, rules: dict, cfg: dict) -> CheckOutput:
    wide = sources.get("wide_table")
    purchase = sources.get("purchase")
    if wide is None or purchase is None:
        return CheckOutput("contracts", "5. Contract", pd.DataFrame([{
            "check": "5. Contract", "status": Status.SKIPPED.value,
            "detail": "Нужны wide table и Purchase."}]))

    p = purchase.df.copy()
    p["ctn"] = p["Container number"].map(norm_container)
    p["sap_contract"] = (p["Main contract"].map(norm_text) + "-" +
                         p["Add contract"].map(norm_text)).str.strip("-")
    cmap = (p[p["ctn"] != ""].groupby("ctn")["sap_contract"]
            .apply(lambda s: sorted(set(s))).to_dict())

    w = wide.df.copy()
    w["ctn"] = w["container_num"].map(norm_container)
    w = w[w["ctn"] != ""].drop_duplicates("ctn")

    rows = []
    for _, r in w.iterrows():
        cd_c = norm_text(r.get("contract"))
        sap_cs = cmap.get(r["ctn"], [])
        ok = any(canon_contract(cd_c) == canon_contract(s) for s in sap_cs)
        if not sap_cs:
            st, dt = Status.REVIEW.value, "контракт SAP не найден"
        elif ok:
            st, dt = Status.OK.value, "контракт совпадает"
        else:
            st, dt = Status.ISSUE.value, "РАСХОЖДЕНИЕ КОНТРАКТА"
        rows.append({
            "check": "5. Contract",
            "container": r["ctn"],
            "decl_num": r.get("decl_num"),
            "cd_contract": cd_c,
            "sap_contract": " | ".join(sap_cs),
            "status": st, "detail": dt})

    df = pd.DataFrame(rows)
    if not df.empty:
        df["_o"] = df["status"].map({Status.ISSUE.value: 0, Status.REVIEW.value: 1,
                                     Status.SKIPPED.value: 2, Status.OK.value: 3})
        df = df.sort_values("_o").drop(columns="_o")
    return CheckOutput("contracts", "5. Contract", df)


register("contracts", "5. Contract", run)
