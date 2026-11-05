# -*- coding: utf-8 -*-
"""Проверки: эталон и каждый случай из аудита (значения данных в вывод не печатаются)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app import checks as reg
from app.config_loader import load_rules
from app.models import Source
from app.normalizers import norm_container, norm_number
from app.services.engine import run as engine_run


def run_check(cid, src, cfg, rules=None):
    return reg.run_check(cid, src, rules if rules is not None else load_rules(cfg["checks"][cid]["rules"]), cfg)


def with_df(src, slot, df):
    out = dict(src)
    out[slot] = Source(slot=slot, name=slot, df=df)
    return out


# ------------------------------------------------------------------ эталон
def test_reference_counts(ref_sources, cfg):
    res = engine_run(ref_sources, cfg)
    s = {r["check_id"]: r for r in res.summary_rows()}
    assert (s["price"]["total"], s["price"]["OK"], s["price"]["ISSUE"]) == (46, 41, 5)
    assert (s["hs_code"]["total"], s["hs_code"]["OK"], s["hs_code"]["REVIEW"]) == (51, 33, 18)
    assert (s["incoterms"]["total"], s["incoterms"]["OK"], s["incoterms"]["REVIEW"]) == (51, 43, 8)
    assert (s["contracts"]["total"], s["contracts"]["OK"], s["contracts"]["ISSUE"], s["contracts"]["REVIEW"]) == (46, 43, 0, 3)
    assert (s["decl_elements"]["OK"], s["decl_elements"]["REVIEW"]) == (17, 4)
    assert res.dq_counts()["ERROR"] == 1


def _single_line_ok_container(ref_sources, cfg):
    p = ref_sources["purchase"].df
    out = run_check("price", ref_sources, cfg).df
    for c in out[out.status == "OK"]["container"]:
        if (p["Container number"].map(norm_container) == c).sum() == 1:
            return c
    raise AssertionError("нет подходящего контейнера")


# ------------------------------------------------------------------ 1. цена
def test_price_currency_mismatch_not_ok(ref_sources, cfg, offline_rates):
    ctn = _single_line_ok_container(ref_sources, cfg)
    w = ref_sources["wide_table"].df.copy()
    w.loc[w["container_num"].map(norm_container) == ctn, "products_currency"] = "USD"
    out = run_check("price", with_df(ref_sources, "wide_table", w), cfg).df
    assert set(out[out.container == ctn].status) <= {"ISSUE", "REVIEW"}


def test_price_second_line_checked(ref_sources, cfg):
    w = ref_sources["wide_table"].df.copy()
    w["_c"] = w["container_num"].map(norm_container)
    # контейнер с несколькими товарными строками одной декларации: добавим вторую строку с ценой +50%
    ctn = _single_line_ok_container(ref_sources, cfg)
    base = w[w["_c"] == ctn].iloc[0].copy()
    base["products_item_number"] = 99
    base["products_price"] = norm_number(base["products_price"]) * 1.5
    w = pd.concat([w, base.to_frame().T], ignore_index=True).drop(columns="_c")
    out = run_check("price", with_df(ref_sources, "wide_table", w), cfg).df
    rows = out[out.container == ctn]
    assert "ISSUE" in set(rows.status) and "OK" in set(rows.status)


def test_price_fx_conversion_review(demo_sources, cfg, offline_rates):
    out = run_check("price", demo_sources, cfg).df
    fx = out[out.detail.str.contains("валюты разные", na=False)]
    assert len(fx) == 1 and fx.iloc[0].status == "REVIEW"


def test_price_unit_from_column(demo_sources, cfg):
    out = run_check("price", demo_sources, cfg).df
    assert (out["cd_unit"] == "kg").all()


# ------------------------------------------------------------------ 2. HS
def test_hs_other_grade_code_is_issue(ref_sources, cfg):
    w = ref_sources["wide_table"].df.copy()
    out = run_check("hs_code", ref_sources, cfg).df
    ok_row = out[(out.status == "OK") & (out.material != "")].iloc[0]
    other = sorted(set(ref_sources["hs_reference"].df["hs_code"].astype(str)) - {ok_row["cd_hs_code"]})
    other = [c for c in other if c[:6] != ok_row["cd_hs_code"][:6]][0]
    w.loc[w["material"].astype(str) == ok_row["material"].split(",")[0], "product_code"] = other
    out2 = run_check("hs_code", with_df(ref_sources, "wide_table", w), cfg).df
    assert "ISSUE" in set(out2[out2.material.str.contains(ok_row["material"].split(",")[0])].status)


def test_hs_empty_code_issue(ref_sources, cfg):
    w = ref_sources["wide_table"].df.copy()
    w.loc[w.index[0], "product_code"] = None
    out = run_check("hs_code", with_df(ref_sources, "wide_table", w), cfg).df
    assert out.detail.str.contains("не заполнен").any()


# ------------------------------------------------------------------ 3. условия поставки
def test_incoterms_synonyms_and_port(ref_sources, cfg):
    w = ref_sources["wide_table"].df.copy()
    i = w.index[0]
    for cd, sap, want in [("CFR", "C&F", "OK"), ("CIF QINGDAO", "CIF", "OK"), ("cif", "CIF ", "OK"),
                          ("FOB", "CIF", "ISSUE")]:
        w.loc[i, ["incoterms", "shipreport_incoterms"]] = [cd, sap]
        out = run_check("incoterms", with_df(ref_sources, "wide_table", w), cfg).df
        dn, ct = w.loc[i, "decl_num"], norm_container(w.loc[i, "container_num"])
        assert set(out[(out.decl_num == dn) & (out.container == ct)].status) == {want}, (cd, sap)


def test_incoterms_qingdao_zero_extra_is_issue(ref_sources, cfg):
    w = ref_sources["wide_table"].df.copy()
    i = w.index[0]
    dn, ct = w.loc[i, "decl_num"], norm_container(w.loc[i, "container_num"])
    w.loc[i, ["incoterms", "shipreport_incoterms", "storage_location", "extra_costs_value"]] = \
        ["CIF", "C&F", "青岛保税区", "0,00"]
    out = run_check("incoterms", with_df(ref_sources, "wide_table", w), cfg).df
    assert set(out[(out.decl_num == dn) & (out.container == ct)].status) == {"ISSUE"}
    w.loc[i, "extra_costs_value"] = "1 250,00"
    out = run_check("incoterms", with_df(ref_sources, "wide_table", w), cfg).df
    assert set(out[(out.decl_num == dn) & (out.container == ct)].status) == {"OK"}


# ------------------------------------------------------------------ 0. полнота
def test_completeness_normalized_numbers(ref_sources, cfg):
    w = ref_sources["wide_table"].df
    ids = w["decl_num"].dropna().astype(str).unique()[:5]
    man = pd.DataFrame({"decl_num": [ids[0] + " ", " " + ids[1]] + [int(x) for x in ids[2:5]]})
    ww = w[w["decl_num"].astype(str).isin(ids)]
    src = with_df(with_df(ref_sources, "wide_table", ww), "sap_vs_cd", man)
    out = run_check("completeness", src, cfg).df
    assert set(out.status) == {"OK"} and len(out) == 5


def test_completeness_float_numbers_review(ref_sources, cfg):
    w = ref_sources["wide_table"].df
    ids = w["decl_num"].dropna().astype(str).unique()[:2]
    man = pd.DataFrame({"decl_num": [float(x) for x in ids]})
    src = with_df(with_df(ref_sources, "wide_table", w[w["decl_num"].astype(str).isin(ids)]), "sap_vs_cd", man)
    out = run_check("completeness", src, cfg).df
    assert "ISSUE" not in set(out.status)


def test_completeness_direction(ref_sources, cfg):
    w = ref_sources["wide_table"].df
    ids = w["decl_num"].dropna().astype(str).unique()[:3]
    man = pd.DataFrame({"decl_num": list(ids[:2]) + ["999999999999999999"]})
    src = with_df(with_df(ref_sources, "wide_table", w[w["decl_num"].astype(str).isin(ids)]), "sap_vs_cd", man)
    both = run_check("completeness", src, cfg, {"direction": "both"}).df
    w2m = run_check("completeness", src, cfg, {"direction": "wide_to_manual"}).df
    assert (both.status == "ISSUE").sum() == 2 and (w2m.status == "ISSUE").sum() == 1


# ------------------------------------------------------------------ 4. элементы декларации
def test_decl_elements_model_normalized(ref_sources, cfg):
    cde = ref_sources["cde_reference"].df
    m = cde["报关单申报型号"].dropna().iloc[0]
    dr = pd.DataFrame({"申报型号": [m, m.replace("-", " "), m.replace(" ", "").upper(), "НЕСУЩЕСТВУЮЩАЯ-1"]})
    out = run_check("decl_elements", with_df(ref_sources, "cd_drafts", dr), cfg).df
    assert list(out.sort_values("declared_model").status.value_counts().sort_index()) and \
        (out.status == "OK").sum() == 3 and (out.status == "ISSUE").sum() == 1


# ------------------------------------------------------------------ 5. контракт
def _ctn_one_sap(ref_sources, cfg):
    p = ref_sources["purchase"].df
    out = run_check("contracts", ref_sources, cfg).df
    for _, r in out[out.status == "OK"].iterrows():
        if (p["Container number"].map(norm_container) == r.container).sum() == 1:
            return r.container
    raise AssertionError


def test_contract_glued_and_homoglyphs(ref_sources, cfg):
    ctn = _ctn_one_sap(ref_sources, cfg)
    w = ref_sources["wide_table"].df.copy()
    p = ref_sources["purchase"].df.copy()
    wi = w.index[w["container_num"].map(norm_container) == ctn]
    pi = p.index[p["Container number"].map(norm_container) == ctn]
    extra = p.loc[pi[0]].copy()
    p.loc[pi[0], ["Main contract", "Add contract"]] = ["SISH.90000", ""]
    extra["Main contract"], extra["Add contract"] = "SISH.90001", ""
    p = pd.concat([p, extra.to_frame().T], ignore_index=True)
    w.loc[wi, "contract"] = "SISH.90000-SISH.90001"
    src = with_df(with_df(ref_sources, "wide_table", w), "purchase", p)
    assert set(run_check("contracts", src, cfg).df.query("container == @ctn").status) == {"OK"}
    # латиница вместо кириллицы
    w2 = ref_sources["wide_table"].df.copy()
    cd = str(w2.loc[wi[0], "contract"])
    w2.loc[wi, "contract"] = cd.replace("С", "C").replace("Х", "X").replace("В", "B")
    out = run_check("contracts", with_df(ref_sources, "wide_table", w2), cfg).df
    assert set(out.query("container == @ctn").status) == {"OK"}


def test_contract_ignore_chars_rule(ref_sources, cfg):
    ctn = _ctn_one_sap(ref_sources, cfg)
    w = ref_sources["wide_table"].df.copy()
    wi = w.index[w["container_num"].map(norm_container) == ctn]
    w.loc[wi, "contract"] = str(w.loc[wi[0], "contract"]).replace(".", "#")
    src = with_df(ref_sources, "wide_table", w)
    assert set(run_check("contracts", src, cfg, {"ignore_chars": " -–—"}).df.query("container == @ctn").status) == {"ISSUE"}
    assert set(run_check("contracts", src, cfg, {"ignore_chars": " -–—.#"}).df.query("container == @ctn").status) == {"OK"}


# ------------------------------------------------------------------ 6. сквозная сверка (демо)
def test_cross_border_demo(demo_sources, cfg, offline_rates):
    out = run_check("cross_border", demo_sources, cfg).df
    ru = out[out.level.str.startswith("ДТ")]
    assert len(ru) == 6
    by_dt = {r.decl_ru: r for _, r in ru.iterrows()}
    assert by_dt["10000010/150926/0000001"].status == "OK"
    assert "цена ДТ/报关单" in by_dt["10000010/160926/0000002"].detail
    assert by_dt["10000010/170926/0000003"].status == "REVIEW"          # совпало после пересчёта валют
    assert "вес" in by_dt["10000010/180926/0000004"].detail and by_dt["10000010/180926/0000004"].status == "ISSUE"
    assert "код" in by_dt["10000010/190926/0000005"].detail             # первые 6 знаков различаются
    assert "контракт" not in by_dt["10000010/190926/0000005"].detail    # латинская B = кириллическая В
    assert "нет в SAP" in by_dt["10000010/200926/0000006"].detail


def test_cross_border_skips_without_ru(ref_sources, cfg):
    out = run_check("cross_border", ref_sources, cfg).df
    assert "SKIPPED" in set(out.status)
    assert "ISSUE" not in set(out.status)         # частичные выпуски из бонда — REVIEW, не ошибка


def test_engine_survives_broken_check(ref_sources, cfg, monkeypatch):
    import app.checks as checks
    title, fn = checks._REGISTRY["price"]
    monkeypatch.setitem(checks._REGISTRY, "price", (title, lambda *a: 1 / 0))
    res = engine_run(ref_sources, cfg)
    assert "Внутренняя ошибка" in res.outputs["price"].df.iloc[0]["detail"]
