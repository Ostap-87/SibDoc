from __future__ import annotations
# -*- coding: utf-8 -*-
"""Проверка 0: полнота — каждая CD есть в обеих таблицах (handbook 1.3).

Номера очищаются перед сравнением (пробелы, «.0», запись числом). Если номер
хранился в Excel числом (18 цифр не помещаются в 15 значащих), совпадение
засчитывается приблизительно и помечается REVIEW — последние цифры могли
потеряться. direction: both | wide_to_manual | manual_to_wide.
"""
import pandas as pd

from app.checks import register
from app.checks._common import Cols, skipped
from app.models import CheckOutput, Status
from app.normalizers import decl_is_lossy, is_blank, norm_decl

TITLE = "0. Completeness"


def _ids(series) -> tuple[dict, set]:
    """-> ({ключ: исходное значение}, {ключи, пришедшие числом})."""
    out, lossy = {}, set()
    for v in series:
        if is_blank(v):
            continue
        k = norm_decl(v)
        if not k:
            continue
        out.setdefault(k, v)
        if decl_is_lossy(v):
            lossy.add(k)
    return out, lossy


def _approx(k: str) -> str:
    try:
        return f"{float(k):.14e}"
    except ValueError:
        return k


def run(sources: dict, rules: dict, cfg: dict) -> CheckOutput:
    wide = sources.get("wide_table")
    manual = sources.get("sap_vs_cd")
    if wide is None or manual is None:
        return skipped("completeness", TITLE, "Нужны оба источника: wide table и «SAP vs CD».")

    W = Cols(cfg, "wide_table", wide.df)
    M = Cols(cfg, "sap_vs_cd", manual.df)
    wcol = W("decl_cn")
    mcol = M("decl_cn") or manual.df.columns[0]
    if wcol is None:
        return skipped("completeness", TITLE, "В wide table нет колонки номера декларации.")
    direction = str(rules.get("direction", "both"))
    max_listed = int(rules.get("max_listed", 50))

    wide_ids, wl = _ids(wide.df[wcol])
    man_ids, ml = _ids(manual.df[mcol])
    lossy = wl | ml
    only_wide = sorted(set(wide_ids) - set(man_ids))
    only_man = sorted(set(man_ids) - set(wide_ids))
    approx = []
    if lossy:  # пробуем сопоставить приблизительно (по 15 значащим цифрам)
        aw = {_approx(k): k for k in only_wide}
        am = {_approx(k): k for k in only_man}
        for a in set(aw) & set(am):
            approx.append((aw[a], am[a]))
            only_wide.remove(aw[a])
            only_man.remove(am[a])

    check_w2m = direction in ("both", "wide_to_manual")
    check_m2w = direction in ("both", "manual_to_wide")
    summary = (f"wide table: {len(wide_ids)} CD, ручная: {len(man_ids)} CD; "
               + (f"нет в ручной: {len(only_wide)}; " if check_w2m else "")
               + (f"нет в wide: {len(only_man)}; " if check_m2w else "")
               + (f"совпали приблизительно: {len(approx)}" if approx else "")).strip("; ")
    # одна строка = одна декларация (из обеих таблиц)
    rows = []
    approx_w = {a: b for a, b in approx}
    for k in sorted(set(wide_ids) | set(man_ids)):
        in_w, in_m = k in wide_ids, k in man_ids
        if k in approx_w:
            st, dt = Status.REVIEW.value, (f"совпала приблизительно с {approx_w[k]}: номер хранился числом, "
                                           "последние цифры могли потеряться — сверить вручную")
        elif k in set(approx_w.values()):
            continue
        elif in_w and in_m:
            st, dt = Status.OK.value, "декларация есть в обеих таблицах"
        elif in_w:
            st, dt = ((Status.ISSUE.value, "нет в ручной таблице «SAP vs CD»") if check_w2m
                      else (Status.SKIPPED.value, "нет в ручной таблице (direction не проверяет)"))
        else:
            st, dt = ((Status.ISSUE.value, "нет в wide table") if check_m2w
                      else (Status.SKIPPED.value, "нет в wide table (direction не проверяет)"))
        rows.append({"check": TITLE, "decl_num": k, "in_wide": "да" if in_w else "нет",
                     "in_manual": "да" if in_m else "нет", "status": st, "detail": dt})
    df = pd.DataFrame(rows) if rows else pd.DataFrame([{"check": TITLE, "status": Status.SKIPPED.value,
                                                         "detail": "в обеих таблицах нет номеров деклараций"}])
    from app.checks._common import sort_df
    return CheckOutput("completeness", TITLE, sort_df(df),
                       meta={"wide": len(wide_ids), "manual": len(man_ids), "summary": summary,
                             "direction": direction, "max_listed": max_listed})


register("completeness", TITLE, run)
