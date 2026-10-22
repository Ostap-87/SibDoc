from __future__ import annotations
# -*- coding: utf-8 -*-
"""Проверка 3: инкотермсы CD vs SAP (shipreport) (handbook, часть III).

Исключение Qingdao/BZA (handbook 3.2.1): склад из списка ключевых слов +
CD=CIF + SAP=C&F + заполненный extra_costs_value -> QUALIFIED (OK).
"""
import pandas as pd
from app.models import CheckOutput, Status
from app.normalizers import norm_container, norm_text
from app.checks import register


def run(sources: dict, rules: dict, cfg: dict) -> CheckOutput:
    wide = sources.get("wide_table")
    if wide is None:
        return CheckOutput("incoterms", "3. Incoterms", pd.DataFrame([{
            "check": "3. Incoterms", "status": Status.SKIPPED.value,
            "detail": "Не загружена wide table."}]))

    keywords = [str(k).lower() for k in (rules.get("qingdao_keywords") or [])]
    allowed = rules.get("qingdao_allowed") or {}
    allowed_cd = str(allowed.get("cd", "CIF")).upper()
    allowed_sap = str(allowed.get("sap", "C&F")).upper()

    rows = []
    for _, r in wide.df.iterrows():
        cd_inc = norm_text(r.get("incoterms")).upper()
        sap_inc = norm_text(r.get("shipreport_incoterms")).upper()
        loc = norm_text(r.get("storage_location")).lower()
        is_qingdao = any(k in loc for k in keywords)
        extra = norm_text(r.get("extra_costs_value"))
        has_extra = extra not in ("", "0", "0.0")

        rec = {"check": "3. Incoterms",
               "decl_num": r.get("decl_num"),
               "container": norm_container(r.get("container_num")),
               "storage_location": norm_text(r.get("storage_location")),
               "cd_incoterms": cd_inc, "sap_incoterms": sap_inc}

        if not sap_inc:
            rec.update(status=Status.REVIEW.value,
                       detail="нет инкотермса SAP для сравнения")
        elif cd_inc == sap_inc:
            rec.update(status=Status.OK.value, detail="инкотермсы совпадают")
        elif (is_qingdao and has_extra
              and cd_inc == allowed_cd and sap_inc == allowed_sap):
            rec.update(status=Status.OK.value,
                       detail=f"Исключение Qingdao/BZA: CD={allowed_cd}, SAP={allowed_sap}, "
                              "доп. расходы заполнены -> QUALIFIED (handbook 3.2.1)")
        else:
            rec.update(status=Status.ISSUE.value,
                       detail=f"расхождение инкотермсов: CD={cd_inc} vs SAP={sap_inc}")
        rows.append(rec)

    df = pd.DataFrame(rows)
    if not df.empty:
        df["_o"] = df["status"].map({Status.ISSUE.value: 0, Status.REVIEW.value: 1,
                                     Status.SKIPPED.value: 2, Status.OK.value: 3})
        df = df.sort_values("_o").drop(columns="_o")
    return CheckOutput("incoterms", "3. Incoterms", df)


register("incoterms", "3. Incoterms", run)
