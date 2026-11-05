from __future__ import annotations
# -*- coding: utf-8 -*-
"""Проверка 3: условия поставки CD vs SAP (shipreport) (handbook, часть III).

Сравниваются условия, а не строки: «C&F» = «CFR», «CIF QINGDAO» = «CIF»
(место поставки отделяется и при compare_place: true сверяется отдельно),
китайские названия и коды 成交方式 переводятся в Incoterms.

Исключение Qingdao/BZA (handbook 3.2.1): склад из списка ключевых слов +
CD=CIF + SAP=CFR + доп. расходы заполнены НЕНУЛЕВОЙ суммой -> QUALIFIED (OK).
«0», «0,00», «0.0» — не заполнено.
"""
import pandas as pd

from app.checks import register
from app.checks._common import Cols, contract_match, skipped, sort_df
from app.models import CheckOutput, Status
from app.normalizers import is_zero_or_blank, norm_container, norm_incoterms, norm_text

TITLE = "3. Incoterms"


def run(sources: dict, rules: dict, cfg: dict) -> CheckOutput:
    wide = sources.get("wide_table")
    if wide is None:
        return skipped("incoterms", TITLE, "Не загружена wide table.")

    syn = {str(k): str(v) for k, v in (rules.get("synonyms") or {}).items()}
    keywords = [str(k).lower() for k in (rules.get("qingdao_keywords") or [])]
    allowed = rules.get("qingdao_allowed") or {}
    allowed_cd = norm_incoterms(allowed.get("cd", "CIF"), syn)[0]
    allowed_sap = norm_incoterms(allowed.get("sap", "C&F"), syn)[0]
    compare_place = bool(rules.get("compare_place", False))

    W = Cols(cfg, "wide_table", wide.df)
    w = wide.df.copy()
    w["_ctn"] = w[W("container")].map(norm_container) if W("container") else ""
    w["_sap_c"] = ["-".join(x for x in (norm_text(W.get(r, "main_contract", "")),
                                        norm_text(W.get(r, "add_contract", ""))) if x)
                   for _, r in w.iterrows()]
    # повторный контейнер: оставляем строки SAP своего контракта (если такие есть)
    keep = []
    key_cols = [c for c in (W("decl_cn"), W("item_no")) if c] + ["_ctn"]
    for _, grp in w.groupby(key_cols, dropna=False, sort=False):
        cd_contract = W.get(grp.iloc[0], "contract", "")
        rel = grp[[bool(contract_match(cd_contract, c)) for c in grp["_sap_c"]]] if cd_contract else grp
        keep.append(rel if not rel.empty else grp)
    w = pd.concat(keep) if keep else w
    dedup = [c for c in (W("decl_cn"), W("incoterms"), W("sap_incoterms"), W("storage_location")) if c]
    w = w.drop_duplicates(["_ctn"] + dedup)

    rows = []
    for _, r in w.iterrows():
        cd_raw = norm_text(W.get(r, "incoterms", ""))
        sap_raw = norm_text(W.get(r, "sap_incoterms", ""))
        cd_t, cd_p = norm_incoterms(cd_raw, syn)
        sap_t, sap_p = norm_incoterms(sap_raw, syn)
        loc = norm_text(W.get(r, "storage_location", ""))
        is_qingdao = any(k and k in loc.lower() for k in keywords)
        extra = W.get(r, "extra_costs", "")
        has_extra = not is_zero_or_blank(extra)

        rec = {"check": TITLE, "decl_num": W.get(r, "decl_cn", ""), "container": r["_ctn"],
               "storage_location": loc, "cd_incoterms": cd_raw, "sap_incoterms": sap_raw,
               "cd_term": cd_t, "sap_term": sap_t, "cd_place": cd_p, "sap_place": sap_p}
        same_note = f" ({cd_raw} = {sap_raw})" if cd_raw.upper() != sap_raw.upper() else ""
        if not sap_t:
            rec.update(status=Status.REVIEW.value, detail="нет условия поставки SAP для сравнения")
        elif not cd_t:
            rec.update(status=Status.ISSUE.value, detail="в декларации не указаны условия поставки")
        elif cd_t == sap_t:
            if compare_place and cd_p and sap_p and cd_p != sap_p:
                rec.update(status=Status.REVIEW.value,
                           detail=f"условие совпадает ({cd_t}), место поставки разное: CD «{cd_p}», SAP «{sap_p}»")
            else:
                rec.update(status=Status.OK.value, detail="условия поставки совпадают" + same_note)
        elif is_qingdao and cd_t == allowed_cd and sap_t == allowed_sap:
            if has_extra:
                rec.update(status=Status.OK.value,
                           detail=f"Исключение Qingdao/BZA: CD={allowed_cd}, SAP={allowed_sap}, доп. расходы "
                                  f"заполнены ({norm_text(extra)}) -> QUALIFIED (handbook 3.2.1)")
            else:
                rec.update(status=Status.ISSUE.value,
                           detail=f"Qingdao/BZA: CD={cd_t} vs SAP={sap_t}, но доп. расходы не заполнены"
                                  f"{' (значение «' + norm_text(extra) + '»)' if norm_text(extra) else ''} — "
                                  "исключение не применяется")
        else:
            rec.update(status=Status.ISSUE.value,
                       detail=f"расхождение условий поставки: CD={cd_raw} vs SAP={sap_raw}")
        rows.append(rec)

    return CheckOutput("incoterms", TITLE, sort_df(pd.DataFrame(rows)))


register("incoterms", TITLE, run)
