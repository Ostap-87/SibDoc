from __future__ import annotations
# -*- coding: utf-8 -*-
"""Проверка 6: сквозная сверка «ДТ РФ ↔ 报关单 КНР ↔ SAP».

Одна строка = один товар российской ДТ (экспорт), связанный:
  - с SAP — по номеру ДТ (Purchase -> «Russian export decl») и контейнерам;
  - с китайской декларацией — по общим контейнерам (при повторном
    использовании контейнера берётся декларация с тем же контрактом,
    затем — ближайшая по дате после ДТ).
Что сверяется: номер ДТ есть в SAP; контракт; вес нетто (ДТ vs SAP, ДТ vs
报关单, когда контейнеры однозначно совпадают); код (первые 6 знаков ТН ВЭД и
кода КНР — общая часть HS); цена за тонну с пересчётом валют по
официальному курсу на дату документа; условия поставки ДТ vs 报关单.

Если ДТ не загружены — сверяется только то, что можно без них: вес 报关单 vs
SAP там, где декларация однозначно покрывает контейнеры (без частичных выпусков).
"""
import numpy as np
import pandas as pd

from app.checks import register
from app.checks._common import (Cols, contract_match, fx_convert, norm_currency_safe, sap_lines,
                                skipped, sort_df, split_containers, worst)
from app.models import CheckOutput, Status
from app.normalizers import (is_blank, nearly_equal, norm_container, norm_date, norm_decl, norm_hs,
                             norm_incoterms, norm_number, norm_text, norm_unit, price_per_ton, ru_dt_date)

TITLE = "6. ДТ ↔ 报关单 ↔ SAP"
OK, ISSUE, REVIEW, SKIP = Status.OK.value, Status.ISSUE.value, Status.REVIEW.value, Status.SKIPPED.value


def _n(v):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), 3)


def _fmt(v, nd=2):
    return "—" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:,.{nd}f}".replace(",", " ")


# ------------------------------------------------------------------ сбор сторон
def ru_items(sources: dict) -> pd.DataFrame:
    src = sources.get("ru_dt")
    if src is None or src.df.empty:
        return pd.DataFrame()
    rows = []
    for _, r in src.df.iterrows():
        g = lambda f: r.get(f) if f in src.df.columns and not is_blank(r.get(f)) else None  # noqa: E731
        decl = norm_decl(g("decl_ru") or "")
        term, place = norm_incoterms(g("incoterms") or "")
        rows.append({"decl_ru": decl, "item_no": norm_text(g("item_no") or ""),
                     "containers": split_containers(g("container")), "contract": norm_text(g("contract") or ""),
                     "hs": norm_hs(g("hs_code") or ""), "net_kg": norm_number(g("net_weight")),
                     "value": norm_number(g("total_value")), "currency": norm_currency_safe(g("currency") or ""),
                     "date": norm_date(g("decl_date") or "") or ru_dt_date(decl), "term": term, "place": place,
                     "file": norm_text(g("source_file") or "")})
    return pd.DataFrame(rows)


def cn_lines(sources: dict, cfg: dict, thr: float) -> pd.DataFrame:
    """Товарные строки китайских деклараций: из cn_dec, иначе из wide table."""
    src = sources.get("cn_dec")
    if src is not None and not src.df.empty:
        df, C, origin = src.df, Cols({}, "cn_dec", src.df), "报关单 (файл брокера)"
    else:
        w = sources.get("wide_table")
        if w is None:
            return pd.DataFrame()
        df, C, origin = w.df, Cols(cfg, "wide_table", w.df), "wide table"
    keys = [c for c in (C("decl_cn"), C("item_no")) if c]
    rows = []
    for _, grp in (df.groupby(keys, dropna=False, sort=False) if keys else [(None, df)]):
        r = grp.iloc[0]
        conts = []
        for v in grp[C("container")] if C("container") else []:
            for c in split_containers(v):
                if c not in conts:
                    conts.append(c)
        price = norm_number(C.get(r, "unit_price"))
        p_t, basis = price_per_ton(price, norm_unit(C.get(r, "unit", "")), thr)
        term, _ = norm_incoterms(C.get(r, "incoterms", ""))
        net = norm_number(C.get(r, "net_weight"))
        rows.append({"decl_cn": norm_decl(C.get(r, "decl_cn", "")), "item_no": norm_text(C.get(r, "item_no", "")),
                     "containers": conts, "contract": norm_text(C.get(r, "contract", "")),
                     "hs": norm_hs(C.get(r, "hs_code", "")), "net_kg": net, "price_t": p_t, "price_basis": basis,
                     "currency": norm_currency_safe(C.get(r, "currency", "")),
                     "date": norm_date(C.get(r, "decl_date", "")), "term": term, "origin": origin})
    return pd.DataFrame(rows)


def _pick_cn(cn: pd.DataFrame, containers: list[str], contract: str, ru_date: str) -> pd.DataFrame:
    if cn.empty or not containers:
        return cn.iloc[0:0]
    hit = cn[[bool(set(c) & set(containers)) for c in cn["containers"]]]
    if hit.empty or hit["decl_cn"].nunique() <= 1:
        return hit
    own = hit[[bool(contract_match(c, contract)) or bool(contract_match(contract, c)) for c in hit["contract"]]]
    if not own.empty and own["decl_cn"].nunique() >= 1:
        hit = own
    if hit["decl_cn"].nunique() > 1 and ru_date:
        later = hit[hit["date"] >= ru_date]
        if not later.empty:
            first = sorted(later["date"].unique())[0]
            hit = later[later["date"] == first]
    return hit


# ------------------------------------------------------------------ проверка
def run(sources: dict, rules: dict, cfg: dict) -> CheckOutput:
    w_tol_pct = float(rules.get("weight_tolerance_pct", 0.5))
    w_tol_kg = float(rules.get("weight_tolerance_kg", 1))
    p_tol_pct = float(rules.get("price_tolerance_pct", 0.5))
    hs_len = int(rules.get("hs_prefix_len", 6))
    inc_mode = str(rules.get("incoterms_mismatch", "review"))
    thr = float(rules.get("per_kg_threshold", 100))
    sap = sap_lines(sources, cfg)
    ru = ru_items(sources)
    cn = cn_lines(sources, cfg, thr)

    if ru.empty and (sap.empty or cn.empty):
        return skipped("cross_border", TITLE, "Нужны ДТ РФ (XML/Excel) и данные SAP, или 报关单 и SAP.")

    rows = []
    if ru.empty:
        rows.append({"check": TITLE, "level": "ДТ РФ", "status": SKIP,
                     "detail": "ДТ РФ не загружены — сверка с российской стороной не выполнялась"})
    else:
        sap_by_dt = {k: g for k, g in sap.groupby("decl_ru")} if not sap.empty else {}
        sap_has_dt = not sap.empty and (sap["decl_ru"] != "").any()
        for dt, items in ru.groupby("decl_ru", sort=False):
            dt_sap = sap_by_dt.get(dt, pd.DataFrame())
            dt_total_net = items["net_kg"].sum(min_count=1)
            multi = len(items) > 1
            for _, it in items.iterrows():
                rows.append(_ru_row(it, dt, dt_sap, sap, sap_has_dt, cn, multi, dt_total_net,
                                    w_tol_pct, w_tol_kg, p_tol_pct, hs_len, inc_mode))

    if bool(rules.get("cn_sap_weight", True)) and not cn.empty and not sap.empty:
        rows.extend(_cn_sap_weight(cn, sap, w_tol_pct, w_tol_kg))

    return CheckOutput("cross_border", TITLE, sort_df(pd.DataFrame(rows)),
                       meta={"ru_items": int(len(ru)), "cn_lines": int(len(cn)), "sap_lines": int(len(sap))})


def _ru_row(it, dt, dt_sap, sap, sap_has_dt, cn, multi, dt_total_net, w_tol_pct, w_tol_kg, p_tol_pct,
            hs_len, inc_mode) -> dict:
    subs: list[tuple[str, str, str]] = []           # (что, статус, пояснение)
    containers = list(it["containers"])
    if not containers and not dt_sap.empty:
        containers = sorted(set(dt_sap["container"]))
    rec = {"check": TITLE, "level": "ДТ РФ ↔ SAP ↔ 报关单", "decl_ru": dt, "item_no": it["item_no"],
           "containers": "; ".join(containers), "contract_ru": it["contract"], "hs_ru": it["hs"],
           "net_ru_kg": _n(it["net_kg"]), "currency_ru": it["currency"], "incoterms_ru": it["term"]}

    # 1. ДТ в SAP
    if sap.empty:
        subs.append(("SAP", SKIP, "нет данных SAP"))
    elif dt_sap.empty:
        if sap_has_dt:
            subs.append(("номер ДТ", ISSUE, f"ДТ {dt} нет в SAP (Purchase → Russian export decl)"))
        else:
            subs.append(("номер ДТ", REVIEW, "в данных SAP нет номеров ДТ — связь только по контейнерам"))
            dt_sap = sap[sap["container"].isin(containers)]
    rel = dt_sap[dt_sap["container"].isin(containers)] if containers and not dt_sap.empty else dt_sap
    rec["contract_sap"] = " | ".join(sorted(set(rel["contract"]) - {""})) if not rel.empty else ""

    # 2. контракт
    if it["contract"] and not rel.empty:
        kinds = [contract_match(it["contract"], c) for c in rel["contract"]]
        if "full" in kinds:
            subs.append(("контракт", OK, "контракт ДТ = SAP"))
        elif "main" in kinds:
            subs.append(("контракт", REVIEW, "совпал только основной контракт"))
        else:
            subs.append(("контракт", ISSUE, f"контракт ДТ {it['contract']} ≠ SAP {rec['contract_sap']}"))
    elif not it["contract"]:
        subs.append(("контракт", REVIEW, "в ДТ не найден номер контракта (документ 03011)"))

    # 3. вес ДТ vs SAP
    if not rel.empty and rel["net_t"].notna().any():
        sap_kg = rel["net_t"].sum() * 1000
        ru_kg = it["net_kg"]
        scope = ""
        if multi and not it["containers"]:          # товары без привязки контейнеров — по ДТ в целом
            ru_kg, scope = dt_total_net, " (по ДТ в целом)"
        rec["net_sap_kg"] = _n(sap_kg)
        if np.isnan(ru_kg):
            subs.append(("вес ДТ/SAP", REVIEW, "в ДТ нет веса нетто"))
        elif nearly_equal(ru_kg, sap_kg, w_tol_kg, w_tol_pct):
            subs.append(("вес ДТ/SAP", OK, f"вес совпадает{scope}"))
        else:
            subs.append(("вес ДТ/SAP", ISSUE, f"вес нетто{scope}: ДТ {_fmt(ru_kg, 1)} кг vs SAP {_fmt(sap_kg, 1)} кг"))

    # 4. цена ДТ vs SAP (за тонну, с пересчётом валют по курсу ЦБ на дату ДТ)
    ru_price_t = it["value"] / (it["net_kg"] / 1000) if it["net_kg"] and not np.isnan(it["net_kg"]) \
        and not np.isnan(it["value"]) and it["net_kg"] > 0 else np.nan
    rec["price_ru_t"] = _n(ru_price_t)
    if not np.isnan(ru_price_t) and not rel.empty and rel["price"].notna().any():
        s = rel.iloc[0]
        val, rate = ru_price_t, ""
        if it["currency"] and s["currency"] and it["currency"] != s["currency"]:
            val, rate = fx_convert(ru_price_t, it["currency"], s["currency"], it["date"], side="ru")
        rec.update(price_sap_t=_n(s["price"]), currency_sap=s["currency"], fx_rate_ru=rate)
        if np.isnan(val):
            subs.append(("цена ДТ/SAP", REVIEW, rate))
        elif any(nearly_equal(val, p, 0.01, p_tol_pct) for p in rel["price"]):
            subs.append(("цена ДТ/SAP", OK if not rate else REVIEW,
                         "цена за тонну совпадает" + (f" после пересчёта ({rate})" if rate else "")))
        else:
            subs.append(("цена ДТ/SAP", ISSUE, f"цена за тонну: ДТ {_fmt(val)} {s['currency']} vs SAP "
                                               f"{', '.join(_fmt(p) for p in rel['price'])}"
                         + (f" ({rate})" if rate else "")))

    # 5. 报关单 КНР
    hit = _pick_cn(cn, containers, it["contract"], it["date"])
    if cn.empty:
        subs.append(("报关单", SKIP, "декларации КНР не загружены"))
    elif hit.empty:
        subs.append(("报关单", REVIEW, "по контейнерам ДТ не найдена китайская декларация"))
    else:
        rec["decl_cn"] = ", ".join(sorted(set(hit["decl_cn"])))
        rec["hs_cn"] = ", ".join(sorted(set(hit["hs"]) - {""}))
        rec["contract_cn"] = " | ".join(sorted(set(hit["contract"]) - {""}))
        rec["incoterms_cn"] = ", ".join(sorted(set(hit["term"]) - {""}))
        if hit["decl_cn"].nunique() > 1:
            subs.append(("报关单", REVIEW, f"контейнеры ДТ есть в нескольких декларациях КНР: {rec['decl_cn']}"))
        # код
        cn_hs = {h[:hs_len] for h in hit["hs"] if h}
        if it["hs"] and cn_hs:
            if it["hs"][:hs_len] in cn_hs:
                subs.append(("код", OK, f"первые {hs_len} знаков кода совпадают"))
            else:
                subs.append(("код", ISSUE, f"код ДТ {it['hs']} и 报关单 {rec['hs_cn']} различаются "
                                           f"в первых {hs_len} знаках"))
        # условия поставки
        cn_terms = {t for t in hit["term"] if t}
        if it["term"] and cn_terms:
            if it["term"] in cn_terms:
                subs.append(("условия", OK, "условия поставки совпадают"))
            else:
                subs.append(("условия", ISSUE if inc_mode == "issue" else REVIEW,
                             f"условия поставки: ДТ {it['term']} vs 报关单 {', '.join(sorted(cn_terms))}"))
        # вес ДТ vs 报关单 — только когда контейнеры совпадают один к одному
        one = hit[hit["containers"].map(lambda c: sorted(c) == sorted(containers))]
        if not one.empty and not np.isnan(it["net_kg"]) and one["net_kg"].notna().any():
            cn_kg = one["net_kg"].sum()
            rec["net_cn_kg"] = _n(cn_kg)
            subs.append(("вес ДТ/报关单", OK if nearly_equal(it["net_kg"], cn_kg, w_tol_kg, w_tol_pct) else ISSUE,
                         "вес ДТ = 报关单" if nearly_equal(it["net_kg"], cn_kg, w_tol_kg, w_tol_pct)
                         else f"вес нетто: ДТ {_fmt(it['net_kg'], 1)} кг vs 报关单 {_fmt(cn_kg, 1)} кг"))
        # цена ДТ vs 报关单
        c0 = hit.iloc[0]
        if not np.isnan(ru_price_t) and not np.isnan(c0["price_t"]):
            val, rate = ru_price_t, ""
            if it["currency"] and c0["currency"] and it["currency"] != c0["currency"]:
                val, rate = fx_convert(ru_price_t, it["currency"], c0["currency"], c0["date"] or it["date"], side="cn")
            rec.update(price_cn_t=_n(c0["price_t"]), currency_cn=c0["currency"])
            if np.isnan(val):
                subs.append(("цена ДТ/报关单", REVIEW, rate))
            elif nearly_equal(val, c0["price_t"], 0.01, p_tol_pct):
                subs.append(("цена ДТ/报关单", OK if not rate else REVIEW,
                             "цена за тонну совпадает" + (f" после пересчёта ({rate})" if rate else "")))
            else:
                subs.append(("цена ДТ/报关单", ISSUE, f"цена за тонну: ДТ {_fmt(val)} {c0['currency']} vs 报关单 "
                                                    f"{_fmt(c0['price_t'])}" + (f" ({rate})" if rate else "")))

    rec["checks"] = "; ".join(f"{name}: {'✓' if st == OK else ('✗' if st == ISSUE else '?')}"
                              for name, st, _ in subs if st != SKIP)
    rec["status"] = worst([st for _, st, _ in subs if st != SKIP]) if any(st != SKIP for _, st, _ in subs) else SKIP
    bad = [f"{name}: {txt}" for name, st, txt in subs if st in (ISSUE, REVIEW)]
    rec["detail"] = "; ".join(bad) if bad else "всё совпадает: " + ", ".join(n for n, s, _ in subs if s == OK)
    return rec


def _cn_sap_weight(cn: pd.DataFrame, sap: pd.DataFrame, w_tol_pct: float, w_tol_kg: float) -> list[dict]:
    """Вес строки 报关单 vs сумма веса контейнеров в SAP — только там, где сравнение однозначно:
    каждый контейнер строки есть только в этой декларации и у него одна строка SAP своего контракта."""
    count = {}
    for conts in cn["containers"]:
        for c in conts:
            count[c] = count.get(c, 0) + 1
    out = []
    for _, r in cn.iterrows():
        conts = r["containers"]
        if not conts or np.isnan(r["net_kg"]) or any(count.get(c, 0) > 1 for c in conts):
            continue
        total, ok = 0.0, True
        for c in conts:
            lines = sap[sap["container"] == c]
            own = lines[[bool(contract_match(r["contract"], k)) for k in lines["contract"]]] if r["contract"] else lines
            use = own if not own.empty else lines
            if len(use) != 1 or np.isnan(use.iloc[0]["net_t"]):
                ok = False
                break
            total += use.iloc[0]["net_t"] * 1000
        if not ok:
            continue
        same = nearly_equal(r["net_kg"], total, w_tol_kg, w_tol_pct)
        if same:
            st, dt = OK, "вес нетто 报关单 = сумме веса контейнеров в SAP"
        elif r["net_kg"] < total:
            # меньше, чем в контейнерах: частичный выпуск (например, со склада бонда) — ошибкой не считаем
            st, dt = REVIEW, (f"в 报关单 {_fmt(r['net_kg'], 1)} кг, в контейнерах по SAP {_fmt(total, 1)} кг "
                              f"({len(conts)} конт.) — похоже на частичный выпуск; проверить остальные "
                              "декларации по этим контейнерам")
        else:
            st, dt = ISSUE, (f"в 报关单 больше, чем в контейнерах: {_fmt(r['net_kg'], 1)} кг vs SAP "
                             f"{_fmt(total, 1)} кг ({len(conts)} конт.)")
        out.append({"check": TITLE, "level": "报关单 ↔ SAP (вес)", "decl_cn": r["decl_cn"], "item_no": r["item_no"],
                    "containers": "; ".join(conts), "contract_cn": r["contract"], "net_cn_kg": _n(r["net_kg"]),
                    "net_sap_kg": _n(total), "status": st, "detail": dt})
    return out


register("cross_border", TITLE, run)
