from __future__ import annotations
# -*- coding: utf-8 -*-
"""Проверка 5: контракт CD vs SAP (main + add contract) по контейнерам.

Одна строка = контейнер × декларация КНР. Сравнение каноническое: латиница
вместо кириллических «двойников» (СХ = CX), без пробелов/дефисов и символов
из правила ignore_chars. «Склеенный» контракт в wide table
(«СХ.35105-396В/СХ.35105-302В», «SISH.12235-SISH.12236») делится на части;
совпадение любой части с любой строкой SAP по контейнеру = OK.
"""
import pandas as pd

from app.checks import register
from app.checks._common import Cols, contract_match, sap_lines, skipped, sort_df
from app.models import CheckOutput, Status
from app.normalizers import has_mixed_scripts, norm_container, norm_text, split_contracts

TITLE = "5. Contract"


def run(sources: dict, rules: dict, cfg: dict) -> CheckOutput:
    wide = sources.get("wide_table")
    if wide is None or sources.get("purchase") is None:
        return skipped("contracts", TITLE, "Нужны wide table и Purchase.")

    ignore = rules.get("ignore_chars")
    ignore = None if ignore is None else str(ignore)
    glued = bool(rules.get("glued_contracts", True))
    main_only = str(rules.get("main_only_match", "review"))

    W = Cols(cfg, "wide_table", wide.df)
    sap = sap_lines(sources, cfg)
    cmap = {c: sorted(set(g["contract"]) - {""}) for c, g in sap.groupby("container")} if not sap.empty else {}

    w = wide.df.copy()
    w["_ctn"] = w[W("container")].map(norm_container) if W("container") else ""
    w = w[w["_ctn"] != ""]
    keys = ["_ctn"] + [c for c in (W("decl_cn"), W("contract")) if c]
    w = w.drop_duplicates(keys)

    rows = []
    for _, r in w.iterrows():
        cd_c = norm_text(W.get(r, "contract", ""))
        sap_cs = cmap.get(r["_ctn"], [])
        parts = split_contracts(cd_c) if glued else [cd_c]
        kinds = {s: (contract_match(cd_c, s, ignore) if glued else
                     ("full" if _plain_eq(cd_c, s, ignore) else "")) for s in sap_cs}
        full = [s for s, k in kinds.items() if k == "full"]
        main = [s for s, k in kinds.items() if k == "main"]
        note = " Внимание: в номере смешаны кириллица и латиница." if has_mixed_scripts(cd_c) else ""
        if not sap_cs:
            st, dt = Status.REVIEW.value, "контракт SAP не найден"
        elif full:
            st = Status.OK.value
            dt = ("контракт совпадает" if len(parts) <= 1 else
                  f"«склеенный» контракт ({len(parts)} части): совпала часть {full[0]}") + note
        elif main and main_only != "issue":
            st = Status.REVIEW.value if main_only == "review" else Status.OK.value
            dt = f"совпал основной контракт ({main[0]}), доп. соглашение в CD не указано" + note
        else:
            st, dt = Status.ISSUE.value, "РАСХОЖДЕНИЕ КОНТРАКТА" + note
        rows.append({"check": TITLE, "container": r["_ctn"], "decl_num": W.get(r, "decl_cn", ""),
                     "cd_contract": cd_c, "cd_contract_parts": " | ".join(parts) if len(parts) > 1 else "",
                     "sap_contract": " | ".join(sap_cs), "status": st, "detail": dt})

    return CheckOutput("contracts", TITLE, sort_df(pd.DataFrame(rows)))


def _plain_eq(a: str, b: str, ignore) -> bool:
    from app.normalizers import canon_contract
    return bool(canon_contract(a, ignore)) and canon_contract(a, ignore) == canon_contract(b, ignore)


register("contracts", TITLE, run)
