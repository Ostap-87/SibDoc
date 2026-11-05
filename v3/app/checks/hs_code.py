from __future__ import annotations
# -*- coding: utf-8 -*-
"""Проверка 2: HS-код декларации КНР против утверждённого кода марки (handbook, часть II).

Код ищется не «в таблице вообще», а для конкретной марки:
  1) по материалу SAP (wide table -> справочник элементов декларации: Material -> HS Code);
  2) если материала нет — по марке из элементов декларации (申报要素, «型号»)
     против справочника HS (declared_model / short_name) и справочника элементов.
Результат:
  OK     — код совпадает с утверждённым для этой марки;
  REVIEW — то же семейство (первые N знаков) / марку определить нельзя, но код есть в таблице;
  ISSUE  — код другой марки или отсутствует в таблице; пустой код.
"""
import pandas as pd

from app.checks import register
from app.checks._common import Cols, contract_match, skipped, sort_df
from app.models import CheckOutput, Status
from app.normalizers import canon_model, norm_container, norm_hs, norm_text

TITLE = "2. HS code"


def _material_key(v) -> str:
    s = norm_text(v)
    return s[:-2] if s.endswith(".0") else s


def run(sources: dict, rules: dict, cfg: dict) -> CheckOutput:
    wide = sources.get("wide_table")
    hs_ref = sources.get("hs_reference")
    cde = sources.get("cde_reference")
    if wide is None:
        return skipped("hs_code", TITLE, "Не загружена wide table.")
    if hs_ref is None and cde is None:
        return skipped("hs_code", TITLE, "Нет справочника HS-кодов (ни загруженного, ни встроенного).")

    plen = int(rules.get("family_prefix_len", 6))
    unknown = str(rules.get("unknown_grade", "review"))
    use_material = "material" in (rules.get("match_by") or ["material", "model"])
    use_model = "model" in (rules.get("match_by") or ["material", "model"])

    # --- справочники: материал -> коды; марка -> коды; все коды
    by_material: dict[str, set] = {}
    by_model: dict[str, set] = {}
    allowed: set = set()
    if cde is not None:
        C = Cols(cfg, "cde_reference", cde.df)
        for _, r in cde.df.iterrows():
            hs = norm_hs(C.get(r, "hs_code", ""))
            if not hs:
                continue
            allowed.add(hs)
            m = _material_key(C.get(r, "material", ""))
            if m:
                by_material.setdefault(m, set()).add(hs)
            for col in (C("model"), "Short Name"):
                if col and col in cde.df.columns and norm_text(r.get(col)):
                    by_model.setdefault(canon_model(r.get(col)), set()).add(hs)
    if hs_ref is not None:
        H = Cols(cfg, "hs_reference", hs_ref.df)
        for _, r in hs_ref.df.iterrows():
            hs = norm_hs(H.get(r, "hs_code", ""))
            if not hs:
                continue
            allowed.add(hs)
            for col in (H("model"), "short_name"):
                if col and col in hs_ref.df.columns and norm_text(r.get(col)):
                    by_model.setdefault(canon_model(r.get(col)), set()).add(hs)
    families = {h[:plen] for h in allowed if len(h) >= plen}
    models_sorted = sorted((k for k in by_model if len(k) >= 4), key=len, reverse=True)

    W = Cols(cfg, "wide_table", wide.df)
    w = wide.df.copy()
    w["_ctn"] = w[W("container")].map(norm_container) if W("container") else ""
    w["_sap_c"] = ["-".join(x for x in (norm_text(W.get(r, "main_contract", "")),
                                        norm_text(W.get(r, "add_contract", ""))) if x)
                   for _, r in w.iterrows()]
    key = [c for c in (W("decl_cn"), W("item_no")) if c] + ["_ctn"]

    rows = []
    for _, grp in w.groupby(key, dropna=False, sort=False):
        first = grp.iloc[0]
        hs = norm_hs(W.get(first, "hs_code", ""))
        cd_contract = W.get(first, "contract", "")
        # материалы строк SAP своего контракта (повторный контейнер тянет чужие строки)
        rel = grp[[bool(contract_match(cd_contract, c)) for c in grp["_sap_c"]]] if cd_contract else grp
        rel = rel if not rel.empty else grp
        materials = sorted({_material_key(W.get(r, "material", "")) for _, r in rel.iterrows()} - {""})
        expected, basis = set(), ""
        if use_material:
            for m in materials:
                if m in by_material:
                    expected |= by_material[m]
            if expected:
                basis = "материал " + ", ".join(materials)
        if not expected and use_model:
            text = canon_model(" ".join(str(W.get(first, f, "")) for f in ("decl_elements", "product_name")))
            for mk in models_sorted:
                if mk in text:
                    expected = set(by_model[mk])
                    basis = f"марка из 申报要素 ({mk})"
                    break

        rec = {"check": TITLE, "decl_num": W.get(first, "decl_cn", ""), "item_no": W.get(first, "item_no", ""),
               "container": first["_ctn"], "product_name": norm_text(W.get(first, "product_name", "")),
               "material": ", ".join(materials), "cd_hs_code": hs,
               "expected_hs": ", ".join(sorted(expected)), "basis": basis}
        if not hs:
            rec.update(status=Status.ISSUE.value, detail="HS-код в декларации не заполнен")
        elif expected:
            if hs in expected:
                rec.update(status=Status.OK.value, detail=f"код совпадает с утверждённым ({basis})")
            elif hs[:plen] in {e[:plen] for e in expected}:
                rec.update(status=Status.REVIEW.value,
                           detail=f"то же семейство (первые {plen} знаков), другой суффикс; для {basis} "
                                  f"утверждён {rec['expected_hs']} — проверить дополнения / маппинг грейдов")
            else:
                rec.update(status=Status.ISSUE.value,
                           detail=f"КОД ДРУГОЙ МАРКИ: для {basis} утверждён {rec['expected_hs']}")
        elif hs in allowed:
            rec.update(status=Status.REVIEW.value if unknown == "review" else Status.OK.value,
                       detail="марку определить не удалось (нет материала SAP и марки в 申报要素); "
                              "код есть в утверждённой таблице — проверить соответствие марке")
        elif hs[:plen] in families:
            rec.update(status=Status.REVIEW.value,
                       detail=f"марка не определена; то же семейство (первые {plen} знаков), другой суффикс")
        else:
            rec.update(status=Status.ISSUE.value,
                       detail="HS-код отсутствует в утверждённой таблице — проверить грейд/завод")
        rows.append(rec)

    return CheckOutput("hs_code", TITLE, sort_df(pd.DataFrame(rows)),
                       meta={"reference_codes": len(allowed), "materials": len(by_material)})


register("hs_code", TITLE, run)
