from __future__ import annotations
# -*- coding: utf-8 -*-
"""Проверка 4: заявленная марка и элементы декларации (申报要素) vs мастер-данные SAP (CDE).

Источник: черновики CD (cd_drafts); если их нет и use_wide_when_no_drafts: true —
фактические декларации из wide table (колонка 申报要素).
Марка сравнивается без учёта регистра, пробелов, дефисов и точек
(«BR-1203 Ti» = «BR 1203 TI»). Для найденной марки дополнительно сверяется
номер CAS из 申报要素 справочника; в отчёт выводятся эталонные элементы,
чтобы специалист видел их рядом с заявленными.
"""
import re

import pandas as pd

from app import fields as fdict
from app.checks import register
from app.checks._common import Cols, skipped, sort_df
from app.models import CheckOutput, Status
from app.normalizers import canon_model, norm_text

TITLE = "4. Decl. elements"
REF_MODEL_COL = "报关单申报型号"
CAS_RX = re.compile(r"\b\d{2,7}-\d{2}-\d\b")


def run(sources: dict, rules: dict, cfg: dict) -> CheckOutput:
    drafts = sources.get("cd_drafts")
    cde_ref = sources.get("cde_reference")
    wide = sources.get("wide_table")
    use_wide = bool(rules.get("use_wide_when_no_drafts", True))
    if cde_ref is None:
        return skipped("decl_elements", TITLE, "Нет справочника элементов декларации (CDE).")
    if drafts is None and not (use_wide and wide is not None):
        return skipped("decl_elements", TITLE, "Нужны черновики CD и справочник элементов декларации (CDE).")

    R = Cols(cfg, "cde_reference", cde_ref.df)
    model_col_ref = R("model") or (REF_MODEL_COL if REF_MODEL_COL in cde_ref.df.columns else None)
    if model_col_ref is None:
        return skipped("decl_elements", TITLE, f"В справочнике CDE нет колонки «{REF_MODEL_COL}».")
    elem_col_ref = R("decl_elements")
    ref: dict[str, dict] = {}
    for _, r in cde_ref.df.iterrows():
        k = canon_model(r.get(model_col_ref))
        if not k:
            continue
        rec = ref.setdefault(k, {"model": norm_text(r.get(model_col_ref)), "cas": set(), "elements": "",
                                 "materials": set()})
        el = norm_text(r.get(elem_col_ref)) if elem_col_ref else ""
        rec["cas"] |= set(CAS_RX.findall(el))
        rec["elements"] = rec["elements"] or el
        if R("material") and norm_text(r.get(R("material"))):
            rec["materials"].add(norm_text(r.get(R("material"))))
    keys_by_len = sorted(ref, key=len, reverse=True)

    patterns = [str(p).lower() for p in (rules.get("model_column_patterns") or ["型号", "model"])]
    if drafts is not None:
        df, src_name = drafts.df, "черновик CD"
        model_col = next((c for c in df.columns if any(p in str(c).lower() for p in patterns)
                          and fdict.field_of_column(c) != "decl_elements"), None)
        elem_col = next((c for c in df.columns if fdict.field_of_column(c) == "decl_elements"), None)
        if model_col is None and elem_col is None:
            return skipped("decl_elements", TITLE, "В файле черновиков не найдена колонка заявленной модели.")
        decl_col = None
    else:
        W = Cols(cfg, "wide_table", wide.df)
        df, src_name = wide.df, "декларация (wide table)"
        model_col, elem_col, decl_col = None, W("decl_elements"), W("decl_cn")
        if elem_col is None:
            return skipped("decl_elements", TITLE, "Нет черновиков CD и колонки 申报要素 в wide table.")
        keys = [c for c in (decl_col, W("item_no")) if c]
        df = df.drop_duplicates(keys + [elem_col]) if keys else df

    rows = []
    for _, r in df.iterrows():
        declared = norm_text(r.get(model_col)) if model_col else ""
        elements = norm_text(r.get(elem_col)) if elem_col else ""
        if not declared and not elements:
            continue
        hit = None
        if declared:
            hit = canon_model(declared) if canon_model(declared) in ref else None
        if hit is None and elements:
            text = canon_model(elements)
            hit = next((k for k in keys_by_len if len(k) >= 4 and k in text), None)
            if hit and not declared:
                declared = ref[hit]["model"]
        rec = {"check": TITLE, "source": src_name, "decl_num": r.get(decl_col) if decl_col else "",
               "declared_model": declared or "(не найдена в 申报要素)", "declared_elements": elements[:500],
               "reference_model": ref[hit]["model"] if hit else "",
               "reference_elements": ref[hit]["elements"][:500] if hit else ""}
        if hit is None:
            unknown_issue = drafts is not None and bool(declared)
            rec.update(status=Status.ISSUE.value if unknown_issue else Status.REVIEW.value,
                       detail=("заявленной модели нет в мастер-данных" if declared else
                               "марку из 申报要素 не удалось сопоставить с мастер-данными — проверить вручную"))
        else:
            notes = []
            cas_decl = set(CAS_RX.findall(elements))
            if cas_decl and ref[hit]["cas"] and not (cas_decl & ref[hit]["cas"]):
                notes.append(f"CAS в декларации {', '.join(sorted(cas_decl))} ≠ мастер-данные "
                             f"{', '.join(sorted(ref[hit]['cas']))}")
            if declared and canon_model(declared) == hit and declared.upper() != ref[hit]["model"].upper():
                notes.append(f"запись марки отличается («{declared}» / «{ref[hit]['model']}»), по сути совпадает")
            bad = [n for n in notes if n.startswith("CAS")]
            rec.update(status=Status.REVIEW.value if bad else Status.OK.value,
                       detail=("модель совпадает с мастер-данными SAP" + ("; " + "; ".join(notes) if notes else "")))
        rows.append(rec)

    df_out = pd.DataFrame(rows) if rows else pd.DataFrame([{
        "check": TITLE, "status": Status.SKIPPED.value, "detail": "Все значения заявленной модели пустые."}])
    return CheckOutput("decl_elements", TITLE, sort_df(df_out), meta={"reference_models": len(ref)})


register("decl_elements", TITLE, run)
