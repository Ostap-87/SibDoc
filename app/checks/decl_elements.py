from __future__ import annotations
# -*- coding: utf-8 -*-
"""Проверка 4: заявленная модель в черновике CD vs мастер-данные SAP (CDE)."""
import pandas as pd
from app.models import CheckOutput, Status
from app.normalizers import norm_text
from app.checks import register

REF_MODEL_COL = "报关单申报型号"


def run(sources: dict, rules: dict, cfg: dict) -> CheckOutput:
    drafts = sources.get("cd_drafts")
    cde_ref = sources.get("cde_reference")
    if drafts is None or cde_ref is None:
        return CheckOutput("decl_elements", "4. Decl. elements", pd.DataFrame([{
            "check": "4. Decl. elements", "status": Status.SKIPPED.value,
            "detail": "Нужны черновики CD и справочник элементов декларации (CDE)."}]))

    patterns = [str(p).lower() for p in (rules.get("model_column_patterns") or ["型号", "model"])]

    ref = cde_ref.df.copy()
    if REF_MODEL_COL not in ref.columns:
        return CheckOutput("decl_elements", "4. Decl. elements", pd.DataFrame([{
            "check": "4. Decl. elements", "status": Status.SKIPPED.value,
            "detail": f"В справочнике CDE нет колонки «{REF_MODEL_COL}»."}]))
    known = set(ref[REF_MODEL_COL].map(norm_text).str.upper()) - {""}

    model_col = next((c for c in drafts.df.columns
                      if any(p in str(c).lower() for p in patterns)), None)
    if model_col is None:
        return CheckOutput("decl_elements", "4. Decl. elements", pd.DataFrame([{
            "check": "4. Decl. elements", "status": Status.SKIPPED.value,
            "detail": "В файле черновиков не найдена колонка заявленной модели."}]))

    rows = []
    for _, r in drafts.df.iterrows():
        m = norm_text(r.get(model_col)).upper()
        if not m:
            continue
        ok = m in known
        rows.append({
            "check": "4. Decl. elements",
            "declared_model": m,
            "status": Status.OK.value if ok else Status.ISSUE.value,
            "detail": ("модель совпадает с мастер-данными SAP"
                       if ok else "заявленной модели нет в мастер-данных")})

    df = pd.DataFrame(rows) if rows else pd.DataFrame([{
        "check": "4. Decl. elements", "status": Status.SKIPPED.value,
        "detail": "Все значения заявленной модели пустые."}])
    if "status" in df.columns and len(df) > 1:
        df["_o"] = df["status"].map({Status.ISSUE.value: 0, Status.REVIEW.value: 1,
                                     Status.SKIPPED.value: 2, Status.OK.value: 3})
        df = df.sort_values("_o").drop(columns="_o")
    return CheckOutput("decl_elements", "4. Decl. elements", df,
                       meta={"reference_models": int(len(known))})


register("decl_elements", "4. Decl. elements", run)
