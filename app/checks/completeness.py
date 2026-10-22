from __future__ import annotations
# -*- coding: utf-8 -*-
"""Проверка 0: полнота — каждая CD есть в обеих таблицах (handbook 1.3)."""
import pandas as pd
from app.models import CheckOutput, Status
from app.normalizers import norm_text
from app.checks import register


def run(sources: dict, rules: dict, cfg: dict) -> CheckOutput:
    wide = sources.get("wide_table")
    manual = sources.get("sap_vs_cd")
    if wide is None or manual is None:
        df = pd.DataFrame([{
            "check": "0. Completeness", "status": Status.SKIPPED.value,
            "detail": "Нужны оба источника: wide table и «SAP vs CD»."}])
        return CheckOutput("completeness", "0. Completeness", df)

    wide_ids = set(wide.df["decl_num"].dropna().astype(str))
    col = "decl_num" if "decl_num" in manual.df.columns else manual.df.columns[0]
    manual_ids = set(manual.df[col].dropna().astype(str))
    only_wide = sorted(wide_ids - manual_ids)
    only_manual = sorted(manual_ids - wide_ids)
    max_listed = rules.get("max_listed", 50)
    ok = not only_wide and not only_manual
    df = pd.DataFrame([{
        "check": "0. Completeness",
        "status": Status.OK.value if ok else Status.ISSUE.value,
        "detail": (f"wide table: {len(wide_ids)} CD, ручная: {len(manual_ids)} CD, "
                   f"нет в ручной: {len(only_wide)}, нет в wide: {len(only_manual)}"),
        "missing_in_manual": ", ".join(only_wide[:max_listed]),
        "missing_in_wide": ", ".join(only_manual[:max_listed])}])
    return CheckOutput("completeness", "0. Completeness", df)


register("completeness", "0. Completeness", run)
