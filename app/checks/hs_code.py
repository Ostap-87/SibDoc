from __future__ import annotations
# -*- coding: utf-8 -*-
"""Проверка 2: HS-коды CD против утверждённой таблицы грейд/завод (handbook, часть II).

Три уровня результата:
  OK     — точное совпадение с утверждённой таблицей;
  REVIEW — то же семейство (первые N знаков), другой суффикс — проверить
           дополнения 2026 года / маппинг грейдов;
  ISSUE  — код отсутствует в утверждённой таблице вовсе.
"""
import pandas as pd
from app.models import CheckOutput, Status
from app.normalizers import norm_hs, norm_text
from app.checks import register


def run(sources: dict, rules: dict, cfg: dict) -> CheckOutput:
    wide = sources.get("wide_table")
    hs_ref = sources.get("hs_reference")
    if wide is None:
        return CheckOutput("hs_code", "2. HS code", pd.DataFrame([{
            "check": "2. HS code", "status": Status.SKIPPED.value,
            "detail": "Не загружена wide table."}]))
    if hs_ref is None:
        return CheckOutput("hs_code", "2. HS code", pd.DataFrame([{
            "check": "2. HS code", "status": Status.SKIPPED.value,
            "detail": "Нет справочника HS-кодов (ни загруженного, ни встроенного)."}]))

    prefix_len = int(rules.get("family_prefix_len", 6))

    ref = hs_ref.df.copy()
    ref["hs"] = ref["hs_code"].map(norm_hs)
    allowed = set(ref["hs"])
    allowed_families = {h[:prefix_len] for h in allowed if len(h) >= prefix_len}

    w = wide.df.copy()
    w["hs"] = w["product_code"].map(norm_hs)

    rows = []
    for _, r in w.iterrows():
        hs = r["hs"]
        if hs in allowed:
            st, dt = Status.OK.value, "точное совпадение с утверждённой таблицей"
        elif hs[:prefix_len] in allowed_families:
            st, dt = Status.REVIEW.value, (
                f"то же семейство (первые {prefix_len} знаков), другой суффикс — "
                "проверить дополнения 2026 / маппинг грейдов")
        else:
            st, dt = Status.ISSUE.value, "HS-код отсутствует в утверждённой таблице — проверить грейд/завод"
        rows.append({
            "check": "2. HS code",
            "decl_num": r.get("decl_num"),
            "product_name": norm_text(r.get("product_name")),
            "cd_hs_code": hs,
            "status": st, "detail": dt})

    df = pd.DataFrame(rows)
    if not df.empty:
        df["_o"] = df["status"].map({Status.ISSUE.value: 0, Status.REVIEW.value: 1,
                                     Status.SKIPPED.value: 2, Status.OK.value: 3})
        df = df.sort_values("_o").drop(columns="_o")
    return CheckOutput("hs_code", "2. HS code", df,
                       meta={"reference_rows": int(len(ref))})


register("hs_code", "2. HS code", run)
